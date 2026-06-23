"""Pydantic request models — the typed request contract.

Mutating/commissioning endpoints take their input from the JSON body (so peer
api-keys and pairing codes never travel in URLs or logs). The AC ``mode`` is the
canonical input; ``system_mode`` is accepted as a documented alias.
"""

from typing import Optional

from pydantic import BaseModel, Field


class NamePayload(BaseModel):
    id: str
    name: str


class NameRemovePayload(BaseModel):
    id: str
    name: str


class TogglePayload(BaseModel):
    id: str


class ControlPayload(BaseModel):
    id: str
    brightness: Optional[float] = None
    temperature: Optional[int] = None


class LevelPayload(BaseModel):
    id: str
    level: Optional[int] = None


class MiredPayload(BaseModel):
    id: str
    mireds: Optional[int] = None


class BatchPayload(BaseModel):
    actions: list[dict]


class ACPayload(BaseModel):
    id: str
    on: Optional[bool] = None
    mode: Optional[int] = Field(default=None, description="Canonical SystemMode input")
    system_mode: Optional[int] = Field(default=None, description="Alias for `mode`")
    setpoint: Optional[float] = None
    fan_speed: Optional[int] = None

    @property
    def effective_mode(self) -> Optional[int]:
        """system_mode wins when both are supplied (documented alias)."""
        return self.system_mode if self.system_mode is not None else self.mode


class BridgePayload(BaseModel):
    ip: str
    port: int
    api_key: Optional[str] = None  # secret in body, never the URL (S1)


class BridgeRemovePayload(BaseModel):
    ip: str
    port: int


class RegisterPayload(BaseModel):
    code: str  # pairing code in body, never the URL (S3)
    ip: Optional[str] = None
    name: Optional[str] = None


class UnregisterPayload(BaseModel):
    node_id: int
