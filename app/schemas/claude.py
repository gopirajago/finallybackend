from datetime import datetime

from pydantic import BaseModel


class ClaudeSettingsBase(BaseModel):
    api_key: str | None = None
    model: str | None = None


class ClaudeSettingsSave(ClaudeSettingsBase):
    pass


class ClaudeSettingsResponse(BaseModel):
    id: int
    user_id: int
    api_key: str | None = None
    model: str
    updated_at: datetime

    model_config = {"from_attributes": True}
