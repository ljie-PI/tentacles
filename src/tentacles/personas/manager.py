"""Persona initialization, versioned edits, and extraction lifecycle."""
from __future__ import annotations

import logging

import yaml

from datetime import datetime
from typing import Any, Awaitable, Callable

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.errors import ConfigError, NotFound, UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.json import extract_json
from tentacles.personas.evolution import evolution_messages, make_proposal
from tentacles.personas.tags import parse_tags, tag_messages
from tentacles.personas.models import PersonaEdit, PersonaProposal, PersonaSeed, PersonaUpdate, PersonaView, persona_name, keywords
from tentacles.store import Store
from tentacles.timeutil import parse_duration, to_utc


log = logging.getLogger(__name__)


class PersonaManager:
    def __init__(self, store: Store, models: ModelRegistry, clock: Callable[[], datetime], update: PersonaUpdate):
        self._store, self._models, self._clock, self._update = store, models, clock, update

    def seed(self, config: AppConfig) -> None:
        def loader(name: str, seed: PersonaSeed) -> Callable[[], PersonaView]:
            def load() -> PersonaView:
                profile = seed.profile
                if seed.profile_file is not None:
                    path = config.resolve(seed.profile_file)
                    try:
                        profile = path.read_text(encoding='utf-8')
                    except (OSError, UnicodeError):
                        raise ConfigError(f'personas.{name}.profile_file: cannot read UTF-8 profile file {path}') from None
                assert profile is not None
                return PersonaView(name=name, version=1, profile=profile, include=seed.include,
                                   exclude=seed.exclude, source='seed', note=seed.note,
                                   created_at=to_utc(self._clock()))
            return load
        self._store.seed_personas([(name, loader(name, seed)) for name, seed in config.personas.items()])

    def list_personas(self) -> list[PersonaView]:
        return self._store.list_personas()

    def get(self, name: str, version: int | None = None) -> PersonaView:
        name = persona_name(name)
        if version is not None and version < 1:
            raise ConfigError('persona version must be positive')
        view = self._store.get_persona(name, version)
        if view is None:
            raise NotFound(f'persona {name!r} or requested version does not exist')
        return view

    def history(self, name: str) -> list[PersonaView]:
        name = persona_name(name)
        rows = self._store.persona_history(name)
        if not rows:
            raise NotFound(f'unknown persona {name!r}')
        return rows

    def export(self, name: str, version: int | None = None) -> str:
        return yaml.safe_dump(self.get(name, version).model_dump(mode='json'),
                              allow_unicode=True, sort_keys=False)

    async def rollback(self, name: str, version: int, note: str | None = None) -> PersonaView:
        old = self.get(name, version)
        edit = PersonaEdit(name=old.name, profile=old.profile, tags=old.tags,
                           include_replace=old.include, exclude_replace=old.exclude,
                           source='rollback', note=note if note is not None else f'rollback to version {version}',
                           tag_status='skipped', run_stats={'rollback_from': version})
        return await self._save(edit)

    async def set(self, name: str, *, profile: str | None = None, skip_tags: bool = False,
                  include_add: list[str] | None = None, include_remove: list[str] | None = None,
                  exclude_add: list[str] | None = None, exclude_remove: list[str] | None = None,
                  note: str | None = None) -> PersonaView:
        if profile is None and not any((include_add, include_remove, exclude_add, exclude_remove)) and note is None:
            raise ConfigError('provide a profile, keyword change, or note')
        try:
            edit = PersonaEdit(name=persona_name(name), profile=profile, note=note or '',
                               include_add=keywords(include_add or []), include_remove=keywords(include_remove or []),
                               exclude_add=keywords(exclude_add or []), exclude_remove=keywords(exclude_remove or []),
                               tag_status='skipped' if skip_tags else 'unchanged')
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        return await self._save(edit, prepare=self._extract_tags if profile is not None and not skip_tags else None)

    async def evolve(self, name: str, *, instruction: str = '', dry_run: bool = False) -> PersonaProposal | PersonaView:
        current = self.get(name)
        instruction = instruction.strip()
        now = to_utc(self._clock())
        feedback = await run_store_call(self._store.eligible_feedback, current.name,
            now - parse_duration(self._update.feedback_window), now, self._update.max_feedback)
        if not instruction and not feedback:
            raise ConfigError('provide an instruction or eligible feedback')
        edit = PersonaEdit(name=current.name, expected_version=current.version, source='llm', tag_status='ok')

        proposal: PersonaProposal | None = None

        async def prepare(edit: PersonaEdit) -> None:
            nonlocal proposal
            model = self._models.resolve('persona')
            edit.run_stats['model'] = model
            system, messages = evolution_messages(current, instruction, feedback, self._update.max_profile_chars)

            try:
                result = await self._models.chat(model).complete(messages, system=system, on_completion=edit.record_completion)
            except UpstreamError as exc:
                raise UpstreamError(f'persona evolution failed ({type(exc).__name__}); persona unchanged') from None
            try:
                proposal = make_proposal(current, extract_json(result.text), len(feedback), self._update.max_profile_chars)
            except ValueError:
                raise UpstreamError('invalid persona evolution output; persona unchanged') from None
            self._proposal_edit(proposal, edit)

        if dry_run:
            await prepare(edit)
            assert proposal is not None
            return proposal
        return await self._save(edit, prepare=prepare)

    async def apply(self, value: PersonaProposal | dict[str, Any]) -> PersonaView:
        try:
            # Validate even model instances: callers may have mutated them after construction.
            proposal = PersonaProposal.model_validate(value.model_dump() if isinstance(value, PersonaProposal) else value)
            proposal.check_profile_length(self._update.max_profile_chars)
        except ValueError:
            raise ConfigError('invalid persona proposal') from None
        self.get(proposal.name)
        edit = PersonaEdit(name=proposal.name, expected_version=proposal.base_version, source='llm', tag_status='ok')
        self._proposal_edit(proposal, edit)
        return await self._save(edit)

    @staticmethod
    def _proposal_edit(proposal: PersonaProposal, edit: PersonaEdit) -> None:
        edit.profile, edit.tags = proposal.profile, proposal.tags
        edit.include_replace, edit.exclude_replace = proposal.include, proposal.exclude
        edit.note = proposal.summary
        edit.run_stats['feedback_count'] = proposal.feedback_count

    async def _save(self, edit: PersonaEdit, *, prepare: Callable[[PersonaEdit], Awaitable[None]] | None = None) -> PersonaView:
        run_id: int | None = None
        completed = False

        def start() -> None:
            nonlocal run_id
            run_id = self._store.start_run('persona', edit.name, self._clock())

        def commit() -> PersonaView:
            nonlocal completed
            assert run_id is not None
            result = self._store.save_persona(edit, self._clock(), run_id)
            completed = True
            return result

        try:
            await run_store_call(start)
            if prepare is not None:
                await prepare(edit)
            return await run_store_call(commit)
        except BaseException as exc:
            if run_id is not None and not completed:
                try:
                    await run_store_call(self._store.finish_run, run_id, status='failed',
                                         now=self._clock(), error=f'persona edit failed ({type(exc).__name__})',
                                         stats=edit.run_stats, cost=edit.cost)
                except Exception as cleanup_error:
                    log.warning('could not finish persona run (%s)', type(cleanup_error).__name__)
            raise

    async def _extract_tags(self, edit: PersonaEdit) -> None:
        old = await run_store_call(self._store.get_persona, edit.name)
        edit.expected_version = old.version if old else 0
        if old is not None and edit.profile == old.profile:
            return
        assert edit.profile is not None
        old_tags = old.tags if old else []
        system, messages = tag_messages(edit.profile, old_tags)
        try:
            name = self._models.resolve('persona')
            edit.run_stats['model'] = name
            result = await self._models.chat(name).complete(messages, system=system, on_completion=edit.record_completion)
            edit.tags = parse_tags(extract_json(result.text), old_tags)
            edit.tag_status = 'ok'
        except Exception as exc:
            # Do not echo prompts, model output or HTTP error bodies into persona history.
            edit.tag_status = 'error'
            edit.tag_error = f'tag extraction failed ({type(exc).__name__}); previous tags retained'
            log.warning('persona tag extraction failed (%s)', type(exc).__name__)
