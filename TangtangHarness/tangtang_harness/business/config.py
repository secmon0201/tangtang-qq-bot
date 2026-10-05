"""Small configuration surface for the migrated deterministic businesses."""
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOURCE_DIR = ROOT / "resources"


@dataclass
class BusinessSettings:
    timezone: str = "Asia/Shanghai"
    public_generator_credit: str = "Generated locally"
    public_short_host: str = ""
    public_site_base_url: str = ""
    command_prefix: str = "#"
    stats_realtime_enabled: bool = True
    asoul_bili_enabled: bool = False
    asoul_bili_target_uids: tuple[str, ...] = ()
    asoul_bili_comment_target_uids: tuple[str, ...] = ()
    asoul_bili_push_dynamic: bool = True
    asoul_bili_push_video: bool = True
    asoul_bili_push_live: bool = True
    asoul_bili_push_comment: bool = True
    asoul_bili_render_cards: bool = True
    asoul_bili_poll_interval_seconds: int = 300
    random_reaction_enabled: bool = False
    random_reaction_group_ids: tuple[int, ...] = ()
    random_repeat_enabled: bool = False
    random_reaction_probability: float = 0.02
    random_reaction_cooldown_seconds: int = 300
    random_repeat_probability: float = 0.01
    random_repeat_cooldown_seconds: int = 300
    random_repeat_message_interval: int = 20
    random_triple_repeat_probability: float = 1.0
    extra: dict = field(default_factory=dict)


settings = BusinessSettings()
