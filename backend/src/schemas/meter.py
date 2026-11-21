from pydantic import BaseModel, Field


class DeviceInput(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    zone: str = Field(min_length=1, max_length=80)
    arm: str = Field(min_length=1, max_length=40)
    generation: int = Field(default=1, ge=1, le=999)
    note: str = Field(default='', max_length=300)


class ReadingInput(BaseModel):
    device_id: int
    reading_date: str = Field(min_length=8, max_length=10)
    entry_total: int = Field(ge=0)
    completed_total: int = Field(ge=0)
    reason: str = Field(default='', max_length=300)


class RecomputeInput(BaseModel):
    reason: str = Field(default='人工触发重算', max_length=300)
