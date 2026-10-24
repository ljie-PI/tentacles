"""Bounded persona text shared by filters and processors."""
from tentacles.personas.models import PersonaView


def render_persona(persona: PersonaView, limit: int = 12000) -> str:
    text = persona.profile + '\n\nPriority tags:\n' + '\n'.join(
        f'{i + 1}. {tag.tag}: {tag.description}' for i, tag in enumerate(persona.tags))
    return text[:limit]
