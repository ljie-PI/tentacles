"""Server options do not import the HTTP adapter into CLI-only operations."""
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ServerSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field(default='127.0.0.1', min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    token: SecretStr = SecretStr('')
    cors_origins: list[str] = Field(default_factory=list)
    scheduler: bool = True
    poll_interval: float = Field(default=30, gt=0, allow_inf_nan=False)
    static_dir: Path = Path('../frontend/dist')

    @field_validator('host')
    @classmethod
    def _host(cls, value: str) -> str:
        if not value.strip() or any(char.isspace() for char in value):
            raise ValueError('server.host must be a nonblank hostname or IP address')
        return value

    @field_validator('cors_origins')
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or
                    parsed.password or parsed.path or parsed.query or parsed.fragment or '*' in value):
                raise ValueError('CORS origins must be explicit http(s) origins without paths or wildcards')
        return values

    @field_validator('token')
    @classmethod
    def _token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token.isascii() or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in token):
            raise ValueError('server.token must be empty or contain printable ASCII without whitespace')
        return value
