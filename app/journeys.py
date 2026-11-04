"""Read-only page journeys built on the existing HTTP probe."""
import asyncio
import re
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from .network_checks import result

JOURNEY_TIMEOUT = 25
MIN_STEPS = 2
MAX_STEPS = 5
PATH = re.compile(r"/[A-Za-z0-9/._~!$&'()*+,;=:@%-]*")


class JourneyStep(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    path: str = Field(min_length=1, max_length=200)
    expected_status: int = Field(default=200, ge=200, le=299)
    required_phrase: str = Field(default='', max_length=120)

    @field_validator('name', 'required_phrase', mode='before')
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                raise ValueError('Enter text on a single line')
            return value.strip()
        return value

    @field_validator('path')
    @classmethod
    def validate_path(cls, path):
        if (not PATH.fullmatch(path) or path.startswith('//')
                or re.search(r'%(?![0-9A-Fa-f]{2})', path)):
            raise ValueError('Use a path such as /pricing, with no query, fragment or host')
        return path


class JourneySettings(BaseModel):
    steps: list[JourneyStep] = Field(min_length=MIN_STEPS, max_length=MAX_STEPS)

    def new_config(self):
        return {'revision': secrets.token_hex(8), 'steps': [step.model_dump() for step in self.steps]}


class JourneyInput(JourneySettings):
    name: str = Field(min_length=2, max_length=100, pattern=r'^[^\r\n]+$')
    origin: str = Field(min_length=10, max_length=300)
    authorized: bool


class JourneyObservation(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    path: str = Field(min_length=1, max_length=200)
    state: Literal['up', 'down', 'unknown']
    http_code: int | None = Field(default=None, ge=100, le=599)
    response_ms: int = Field(ge=0, le=60000)
    error: str | None = Field(default=None, max_length=160)


class JourneyTrace(BaseModel):
    revision: str = Field(pattern=r'^[0-9a-f]{16}$')
    steps: list[JourneyObservation] = Field(min_length=1, max_length=MAX_STEPS)


async def journey_origin(value, validate_url):
    parts = urlsplit(value)
    if parts.path not in ('', '/') or '?' in value or '#' in value:
        raise ValueError('Enter the site origin, such as https://example.com. Add page paths as steps.')
    host, _, _, _, scheme = await validate_url(value)
    return f'{scheme}://{host}'


async def probe_journey(origin, config, probe, check_connectivity, *, public_only=False):
    started = time.monotonic()
    observed = []
    for step in config['steps']:
        step_started = time.monotonic()
        try:
            async with asyncio.timeout(JOURNEY_TIMEOUT - (step_started - started)):
                check = await probe(origin + step['path'], public_only=public_only,
                                    expected_status=step['expected_status'],
                                    required_phrase=step['required_phrase'])
        except TimeoutError:
            check = result(error=f'Journey exceeded its {JOURNEY_TIMEOUT}-second check budget')
        duration = int((time.monotonic() - step_started) * 1000)
        check = await check_connectivity(check)
        observed.append({
            'name': step['name'], 'path': step['path'], 'state': check['state'],
            'http_code': check['http_code'], 'response_ms': duration,
            'error': (check['error'] or '')[:160] or None,
        })
        if check['state'] != 'up':
            break

    completed = len(observed)
    total = len(config['steps'])
    if check['state'] == 'up':
        detail = f'All {total} journey steps passed'
        error = None
    else:
        label = f'Step {completed}/{total} · {step["name"]}'
        detail = f'{label} · {"Inconclusive" if check["state"] == "unknown" else "Failed"}'
        error = f'{label}: {check["error"]}'
    return {
        **check, 'response_ms': sum(step['response_ms'] for step in observed),
        'error': error, 'detail': detail,
        'journey_trace': {'revision': config['revision'], 'steps': observed},
    }
