from datetime import date, datetime, time
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    department: str = Field(min_length=1, max_length=100)
    job_role: str = Field(min_length=1, max_length=100)
    status: str = Field(default="active", max_length=30)

class EmployeeOut(EmployeeIn, ORMModel):
    id: int

class ShiftIn(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    start_time: time
    end_time: time

class ShiftOut(ShiftIn, ORMModel):
    id: int

class AssignmentIn(BaseModel):
    employee_id: int
    shift_id: int
    effective_from: date = Field(default_factory=date.today)

class CheckInOut(BaseModel):
    employee_id: int

class LeaveIn(BaseModel):
    employee_id: int
    start_date: date
    end_date: date
    leave_type: str = Field(default="casual", max_length=50)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

class LeaveReview(BaseModel):
    status: str

class PerformanceIn(BaseModel):
    employee_id: int
    review_period: str = Field(min_length=2, max_length=50)
    rating: float = Field(ge=1, le=5)
    feedback: str = Field(min_length=1, max_length=5000)

class NotificationOut(ORMModel):
    id: int
    employee_id: int | None
    title: str
    message: str
    category: str
    is_read: bool
    created_at: datetime
