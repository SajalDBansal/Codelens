"""User settings, OS credential storage, and scoped settings for the existing engine."""
import hashlib
import os
from contextlib import contextmanager
from typing import Literal
import keyring
from pydantic import Field, field_validator, model_validator
from .. import config
from .engine_options import EngineOptions
from .state import Record, StateError, home, locked, read_json, write_json
EMBEDDING_MODELS = {'text-embedding-3-small': 1536, 'text-embedding-3-large': 3072, 'text-embedding-ada-002': 1536}
REASONING_MODELS = ['gpt-4.1-nano', 'gpt-4.1-mini', 'gpt-5-mini']

class Settings(Record):
    schema_version: Literal[2] = 2
    provider: Literal['openai'] = 'openai'
    embedding_model: str = 'text-embedding-3-small'
    embedding_dimensions: int = Field(default=1536, strict=True, ge=1)
    reasoning_model: str = 'gpt-4.1-nano'
    credential_source: Literal['keyring'] = 'keyring'
    engine: EngineOptions = Field(default_factory=EngineOptions)

    @model_validator(mode='before')
    @classmethod
    def upgrade_saved_settings(cls, values):
        if isinstance(values, dict) and values.get('schema_version') == 1:
            values = dict(values)
            values['schema_version'] = 2
            if values.get('credential_source') == 'env':
                values['credential_source'] = 'keyring'
        return values

    @field_validator('reasoning_model')
    @classmethod
    def valid_reasoning_model(cls, value):
        if not value or len(value) > 200 or any((c.isspace() for c in value)):
            raise ValueError('Use an OpenAI Chat Completions model ID without spaces.')
        if value.startswith('text-embedding-'):
            raise ValueError('An embedding model cannot generate answers.')
        return value

    @model_validator(mode='after')
    def valid_embedding(self):
        maximum = EMBEDDING_MODELS.get(self.embedding_model)
        if maximum is None:
            raise ValueError('Select one of the embedding models listed by codelens models.')
        if not 1 <= self.embedding_dimensions <= maximum:
            raise ValueError(f'Dimensions must be between 1 and {maximum}.')
        if self.embedding_model == 'text-embedding-ada-002' and self.embedding_dimensions != 1536:
            raise ValueError('text-embedding-ada-002 requires 1536 dimensions.')
        return self

def load_settings() -> Settings | None:
    return read_json(home() / 'config.json', Settings)

def save_settings(settings: Settings) -> None:
    with locked(home() / '.state.lock'):
        write_json(home() / 'config.json', settings)

def credential_account() -> str:
    return hashlib.sha256(os.path.normcase(str(home())).encode()).hexdigest()

def store_key(value: str) -> None:
    if not value.strip():
        raise StateError('The API key cannot be empty.')
    try:
        keyring.set_password('codelens.openai', credential_account(), value.strip())
    except Exception as error:
        raise StateError('The OS credential store is unavailable. Enable a supported keyring backend and retry codelens config update api_key. No key was written to JSON.') from error

def api_key(settings: Settings) -> str:
    try:
        value = keyring.get_password('codelens.openai', credential_account()) or ''
    except Exception as error:
        raise StateError('Cannot read the OS credential store. Run codelens init.') from error
    if not value or value.strip() == 'sk-...':
        raise StateError('No saved OpenAI API key. Run codelens config update api_key.')
    return value.strip()

@contextmanager
def engine_settings(settings: Settings, *, profile: tuple[str, int] | None=None):
    """The current engine is synchronous. Restore globals even if a command fails."""
    model, dimensions = profile or (settings.embedding_model, settings.embedding_dimensions)
    values = {**settings.engine.overrides(), 'OPENAI_API_KEY': api_key(settings), 'EMBEDDING_MODEL': model, 'EMBEDDING_DIM': dimensions, 'CHAT_MODEL': settings.reasoning_model}
    previous = {key: getattr(config, key) for key in values}
    try:
        for key, value in values.items():
            setattr(config, key, value)
        config.validate()
        yield
    finally:
        for key, value in previous.items():
            setattr(config, key, value)
