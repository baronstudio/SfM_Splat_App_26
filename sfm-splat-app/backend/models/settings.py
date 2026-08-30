from pydantic import BaseModel

class Settings(BaseModel):
    rc_exe_path: str
    lfs_exe_path: str
    ffmpeg_path: str
    supersplat_url: str
