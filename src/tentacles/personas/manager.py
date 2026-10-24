"""Persona initialization, versioned edits, and extraction lifecycle."""
from __future__ import annotations

import logging

import yaml

from datetime import datetime
from typing import Callable

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.errors import ConfigError, NotFound
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.json import extract_json
from tentacles.personas.tags import parse_tags, tag_messages
from tentacles.personas.models import PersonaEdit, PersonaSeed, PersonaView, persona_name, keywords
from tentacles.store import Store
from tentacles.timeutil import to_utc


log = logging.getLogger(__name__)


class PersonaManager:
    def __init__(self, store: Store, models: ModelRegistry, clock: Callable[[], datetime]):
        self._store, self._models, self._clock = store, models, clock

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
        return await self._save(edit, extract=profile is not None and not skip_tags)

    async def _save(self, edit: PersonaEdit, *, extract: bool = False) -> PersonaView:
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
            if extract:
                await self._extract_tags(edit)
            return await run_store_call(commit)
        except BaseException as exc:
            if run_id is not None and not completed:
                try:
                    await run_store_call(self._store.finish_run, run_id, status='failed',
                                         now=self._clock(), error=f'persona edit failed ({type(exc).__name__})')
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
            result = await self._models.chat(name).complete(messages, system=system)
            edit.run_stats['usage'] = result.usage.model_dump()
            edit.cost = result.cost
            edit.tags = parse_tags(extract_json(result.text), old_tags)
            edit.tag_status = 'ok'
        except Exception as exc:
            # Do not echo prompts, model output or HTTP error bodies into persona history.
            edit.tag_status = 'error'
            edit.tag_error = f'tag extraction failed ({type(exc).__name__}); previous tags retained'
            log.warning('persona tag extraction failed (%s)', type(exc).__name__)
