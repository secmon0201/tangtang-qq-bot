"""Runtime settings needed by the independent live-schedule service."""


class PassiveSettingsStore:
    def __init__(self, store):
        self.store = store

    def is_game_globally_enabled(self) -> bool:
        return bool(self.store.get_setting("mini_games_enabled", True))
