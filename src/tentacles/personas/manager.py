"""Persona initialization, versioned edits, and extraction lifecycle."""
from __future__ import annotations

import logging

import yaml

from datetime import datetime
from typing import Any, Awaitable, Callable

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.errors import ConfigError, Conflict, NotFound, UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.json import extract_json
from tentacles.llm.types import UsageTotals
from tentacles.personas.evolution import evolution_messages, make_proposal
from tentacles.personas.tags import parse_tags, tag_messages
from tentacles.personas.models import PersonaEdit, PersonaProposal, PersonaSeed, PersonaUpdate, PersonaView, persona_name, keywords
from tentacles.runs import RunLifecycle, RunOperation
from tentacles.store import RunWriter, Store
from tentacles.timeutil import parse_duration, to_utc


log = logging.getLogger(__name__)


def _expected_version(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
        raise ConfigError('expected persona version must be a nonnegative int64')
    return value


class PersonaManager:
    def __init__(self, store: Store, models: ModelRegistry, clock: Callable[[], datetime], update: PersonaUpdate):
        self._store, self._models, self._clock, self._update = store, models, clock, update
        self._runs = RunLifecycle(store, clock)

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
        if version is not None and (type(version) is not int or not 1 <= version <= 2**63 - 1):
            raise ConfigError('persona version must be a positive int64')
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

    async def rollback(self, name: str, version: int, note: str | None = None,
                       expected_version: int | None = None) -> PersonaView:
        old = self.get(name, version)
        edit = PersonaEdit(name=old.name, profile=old.profile, tags=old.tags,
                           include_replace=old.include, exclude_replace=old.exclude,
                           source='rollback', expected_version=_expected_version(expected_version), note=note if note is not None else f'rollback to version {version}',
                           tag_status='skipped', run_stats={'rollback_from': version})
        return await self._save(edit)

    async def set(self, name: str, *, profile: str | None = None, skip_tags: bool = False,
                  include_add: list[str] | None = None, include_remove: list[str] | None = None,
                  exclude_add: list[str] | None = None, exclude_remove: list[str] | None = None,
                  note: str | None = None, expected_version: int | None = None) -> PersonaView:
        if profile is None and not any((include_add, include_remove, exclude_add, exclude_remove)) and note is None:
            raise ConfigError('provide a profile, keyword change, or note')
        try:
            edit = PersonaEdit(name=persona_name(name), profile=profile, note=note or '',
                               expected_version=_expected_version(expected_version),
                               include_add=keywords(include_add or []), include_remove=keywords(include_remove or []),
                               exclude_add=keywords(exclude_add or []), exclude_remove=keywords(exclude_remove or []),
                               tag_status='skipped' if skip_tags else 'unchanged')
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        return await self._save(edit, prepare=self._extract_tags if profile is not None and not skip_tags else None)

    async def evolve(self, name: str, *, instruction: str = '', dry_run: bool = False,
                     expected_version: int | None = None, use_feedback: bool = True) -> PersonaProposal | PersonaView:
        current = self.get(name)
        expected_version = _expected_version(expected_version)
        if expected_version is not None and expected_version != current.version:
            raise Conflict('persona changed; refresh before generating a proposal')
        instruction = instruction.strip()
        if type(use_feedback) is not bool:
            raise ConfigError('use_feedback must be a boolean')
        feedback = []
        if use_feedback:
            now = to_utc(self._clock())
            feedback = await run_store_call(self._store.eligible_feedback, current.name,
                now - parse_duration(self._update.feedback_window), now, self._update.max_feedback)
        if not instruction and not feedback:
            raise ConfigError('provide an instruction or eligible feedback')
        edit = PersonaEdit(name=current.name, expected_version=current.version, source='llm', tag_status='ok')

        proposal: PersonaProposal | None = None

        async def prepare(edit: PersonaEdit, metrics: UsageTotals) -> None:
            nonlocal proposal
            model = self._models.resolve('persona')
            system, messages = evolution_messages(current, instruction, feedback, self._update.max_profile_chars)

            try:
                result = await self._models.chat(model).complete(messages, system=system, on_completion=metrics.record)
            except UpstreamError as exc:
                raise UpstreamError(f'persona evolution failed ({type(exc).__name__}); persona unchanged') from None
            try:
                proposal = make_proposal(current, extract_json(result.text), len(feedback), self._update.max_profile_chars)
            except ValueError:
                raise UpstreamError('invalid persona evolution output; persona unchanged') from None
            self._proposal_edit(proposal, edit)

        if dry_run:
            await prepare(edit, UsageTotals())
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

    async def _save(self, edit: PersonaEdit, *,
                    prepare: Callable[[PersonaEdit, UsageTotals], Awaitable[None]] | None = None) -> PersonaView:
        view: PersonaView | None = None

        async def work(op: RunOperation) -> None:
            op.stats = edit.run_stats
            if prepare is not None:
                await prepare(edit, op.metrics)

            def save(writer: RunWriter) -> None:
                nonlocal view
                view = writer.save_persona(edit)
                op.stats.update(version=view.version, tag_status=view.tag_status, tag_error=view.tag_error)

            # Tag extraction degradation is a saved Persona, not a failed edit.
            await op.commit(save, error=edit.tag_error, status='ok')

        await self._runs.execute(('persona', edit.name), work,
                                 failure_message='persona edit failed', raise_errors=True)
        assert view is not None
        return view

    async def _extract_tags(self, edit: PersonaEdit, metrics: UsageTotals) -> None:
        old = await run_store_call(self._store.get_persona, edit.name)
        version = old.version if old else 0
        if edit.expected_version is not None and edit.expected_version != version:
            raise Conflict('persona changed; refresh before extracting tags')
        edit.expected_version = version
        if old is not None and edit.profile == old.profile:
            return
        assert edit.profile is not None
        old_tags = old.tags if old else []
        system, messages = tag_messages(edit.profile, old_tags)
        try:
            name = self._models.resolve('persona')
            result = await self._models.chat(name).complete(messages, system=system, on_completion=metrics.record)
            edit.tags = parse_tags(extract_json(result.text), old_tags)
            edit.tag_status = 'ok'
        except Exception as exc:
            # Do not echo prompts, model output or HTTP error bodies into persona history.
            edit.tag_status = 'error'
            edit.tag_error = f'tag extraction failed ({type(exc).__name__}); previous tags retained'
            log.warning('persona tag extraction failed (%s)', type(exc).__name__)
