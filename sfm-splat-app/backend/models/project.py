import json
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


class Project(SQLModel, table=True):
    id: Optional[str] = Field(
        default_factory=lambda: uuid4().hex[:8], primary_key=True
    )
    name: str
    slug: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    current_step: int = 0
    step_status: str = "{}"
    input_video_path: Optional[str] = None
    frame_count: int = 0
    settings_json: str = "{}"
    error_message: Optional[str] = None
    # Archived: the files live in a .zip under projects/_archives/ and the row
    # stays in the list, read-only, until it is restored (CLAUDE.md §14).
    archived_at: Optional[datetime] = None
    archive_path: Optional[str] = None

    def get_step_status(self) -> dict:
        return json.loads(self.step_status)

    def set_step_status(self, status: dict) -> None:
        self.step_status = json.dumps(status)
