from datetime import datetime

from pydantic import BaseModel


class BrokerSettingsBase(BaseModel):
    api_key: str | None = None
    api_secret: str | None = None


class BrokerSettingsSave(BrokerSettingsBase):
    pass


class BrokerSettingsResponse(BrokerSettingsBase):
    id: int
    user_id: int
    broker: str
    access_token: str | None = None
    token_generated_at: datetime | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenerateTokenResponse(BaseModel):
    access_token: str
    token_generated_at: datetime
