import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv('DEEPSEEK_API_KEY', '').strip())
    base_url: str = field(default_factory=lambda: os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').strip())
    model: str = field(default_factory=lambda: os.getenv('DEEPSEEK_MODEL', '').strip())
    thinking: str = field(default_factory=lambda: os.getenv('DEEPSEEK_THINKING', 'auto').strip().lower())
    max_output_tokens: int = field(default_factory=lambda: int(os.getenv('MAX_OUTPUT_TOKENS', '131072')))
    token: str = field(default_factory=lambda: os.getenv('APP_TOKEN', '').strip())
    host: str = field(default_factory=lambda: os.getenv('HOST', '127.0.0.1'))
    port: int = field(default_factory=lambda: int(os.getenv('PORT', '8765')))
    max_images: int = field(default_factory=lambda: int(os.getenv('MAX_IMAGES_PER_TASK', '20')))
    max_bytes: int = field(default_factory=lambda: int(os.getenv('MAX_TASK_IMAGE_BYTES', '41943040')))
    data_dir: Path = ROOT / 'server' / 'data'

    def validate(self) -> None:
        if self.host != '127.0.0.1' or self.port != 8765:
            raise ValueError('服务仅允许监听 127.0.0.1:8765，请修正 HOST / PORT。')
        if not 1 <= self.max_images <= 20 or self.max_bytes <= 0:
            raise ValueError('图片数量必须为 1～20，图片大小限制必须大于零。')
        if self.thinking not in {'auto', 'enabled', 'disabled'}:
            raise ValueError('DEEPSEEK_THINKING 必须为 auto、enabled 或 disabled。')
        if not 1024 <= self.max_output_tokens <= 131072:
            raise ValueError('MAX_OUTPUT_TOKENS 必须为 1024～131072。')

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / 'tmp'
