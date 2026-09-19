"""Advertised MCP shapes; D1 and consumer.normalize_request own validation."""
import copy
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .consumer_transport import bounded_json, descriptors
from pydantic.json_schema import SkipJsonSchema

Token = Annotated[str, Field(max_length=64, pattern=r'^[a-z][a-z0-9_]*$')]
Capability = Annotated[str, Field(max_length=64, pattern=r'^[a-z][a-z0-9_.-]*$')]
Descriptor = Annotated[str, Field(max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class HardRequirements(StrictModel):
    content: list[Token] = Field(default_factory=list, max_length=32)
    representations: list[Token] = Field(default_factory=list, max_length=32)
    required_capabilities: list[Capability] = Field(default_factory=list, max_length=32)
    transparent_background: bool | None = None
    save_blend: bool | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    camera_policy: Literal['preserve_input', 'authored'] | None = None


class VisualBrief(StrictModel):
    representations: list[Token] = Field(default_factory=list, max_length=32)
    material_family: Literal['clay', 'frosted', 'milky', 'dried_paint', 'directional_ink'] | None = None
    palette: list[Descriptor] = Field(default_factory=list, max_length=32,
        description='Canonical #RRGGBB or linear_rgb:[r,g,b], with finite channels from 0 to 16.')
    background: Descriptor | None = Field(default=None, description='solid, gradient, transparent, or #RRGGBB')
    lighting: Literal['area', 'sun', 'point', 'spot'] | None = None
    edges: Literal['smooth', 'mixed', 'rounded'] | None = None
    composition: Literal['structure', 'focus_style', 'depth_of_field'] | None = None
    projection: Literal['ORTHO', 'PERSP'] | None = None
    ligand_treatment: Literal['sticks'] | None = None


class HostedRenderRequest(StrictModel):
    format_version: Literal[1] = 1
    request_key: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$',
        description='Generate a fresh opaque key for each new user render request. Keep it only for retries of that submission; never reuse a previous request key or put a filename, URL or credential here.')
    # Accept previously advertised requests for recovery, without exposing a knob.
    rerender: SkipJsonSchema[bool] = False
    preset: str | None = None
    revision: str | int | None = None
    hard_requirements: HardRequirements = Field(default_factory=HardRequirements)
    visual_brief: VisualBrief = Field(default_factory=VisualBrief)
    overrides: dict = Field(default_factory=dict, description='Explicit existing style-schema edits; filenames and inactive controls are rejected.')
    allow_low_fit_approximation: bool = False
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    samples: int | None = Field(default=None, ge=1)
    transparent_background: bool | None = Field(default=None, description='Omit to preserve the preset background. Set true only when the user asks for transparency.')
    save_blend: bool | None = Field(default=None, description='PNG is always requested; a Blender scene requires explicit true.')
    color_follow: bool | None = None
    rough: bool = False
    animation: Literal['none', 'rock', '360'] | None = Field(default=None, description='One animation job, never separate still requests per frame. Omit for a still.')
    duration: float | None = Field(default=None, gt=0, le=15)
    fps: int | None = Field(default=None, ge=1, le=60)
    rock_angle: float | None = Field(default=None, gt=0, le=45)
    gif: bool | None = Field(default=None, description='Return the rendered GIF directly. Animations default to GIF, 960x540, 48 samples, 3 seconds and 12 fps; explicit settings override those defaults.')

    @model_validator(mode='before')
    @classmethod
    def bounded_transport(cls, value):
        bounded_json(value)
        return descriptors(copy.deepcopy(value))

    @field_validator('format_version', mode='before')
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError('format_version must be integer 1')
        return value

    def request_dict(self):
        return self.model_dump(exclude_none=True)


class TrustedRenderRequest(HostedRenderRequest):
    style_doc: dict | None = Field(default=None,
        description='Complete existing validated style JSON, exclusive with preset selection. No executable code or Blender input.')
