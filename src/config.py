import json
import sys
import re
from typing import Any, Dict, List
from dataclasses import dataclass, field

CONFIG_FILE_NAME = "config.json"


@dataclass
class RadarrConfig:
    api_key: str
    base_url: str
    exempt_tag_names: List[str] = field(default_factory=list)

@dataclass
class DynamicLoad:
    enabled: bool
    schedule_interval: int
    episodes_to_load: int

@dataclass
class SonarrConfig:
    api_key: str
    base_url: str
    monitor_continuing_series: bool
    keep_pilot_episodes: bool
    exempt_tag_names: List[str] = field(default_factory=list)
    dynamic_load: DynamicLoad = field(default_factory=DynamicLoad)

@dataclass
class OverseerrConfig:
    api_key: str
    base_url: str
    fetch_limit: int


@dataclass
class PlexConfig:
    base_url: str
    token: str
    refresh: bool

@dataclass
class Config:
    radarr: RadarrConfig
    sonarr: SonarrConfig
    overseerr: OverseerrConfig
    plex: PlexConfig
    last_watched_deletion_threshold: int
    unwatched_deletion_threshold: int
    dry_run: bool
    schedule_interval: int

    def __init__(self):
        # Default values are set
        self.radarr = RadarrConfig("", "http://host:port/api/v3", [])
        self.sonarr = SonarrConfig("", "https://host:port/api/v3", True, True, [], DynamicLoad(False, 600, 5))
        self.overseerr = OverseerrConfig("", "http://host:port/api/v1", 10)
        self.plex = PlexConfig("http://host:port", "", True)
        self.last_watched_deletion_threshold = 7776000
        self.unwatched_deletion_threshold = 2592000
        self.dry_run = True
        self.schedule_interval = 86400

        # Read the config file
        config = self._get_config()

        # Set the configuration values if provided
        try:
            self._parse_config(config)
        except TypeError as err:
            print("Error in configuration file:")
            print(err)
            sys.exit()

    def _get_config(self) -> Dict[str, Any]:
        """
        Reads the configuration file and returns its contents as a dictionary.

        Returns:
            A dictionary containing the configuration values.
        """
        config = {}
        try:
            with open(CONFIG_FILE_NAME, encoding="utf-8") as file:
                config = json.load(file)
        except FileNotFoundError:
            pass

        return config

    def _parse_config(self, config: Dict[str, Any]) -> None:
        """
        Parses the configuration dictionary and sets the corresponding attributes of the Config object.

        Args:
            config: A dictionary containing the configuration values.

        Raises:
            KeyError: If any required configuration keys are missing.
            TypeError: If any of the configuration values are of the wrong type.
            ValueError: If any of the configuration values are invalid.
        """
        # List of required configuration keys
        required_keys = [
            "radarr",
            "sonarr",
            "overseerr",
            "plex",
            "last_watched_deletion_threshold",
            "unwatched_deletion_threshold",
            "dry_run",
            "schedule_interval",
        ]

        # Check for missing keys
        for key in required_keys:
            if key not in config:
                raise KeyError(f"Missing required configuration key: {key}")

        # Parse and validate configuration values
        try:
            self.radarr = RadarrConfig(**config["radarr"])
            self.sonarr = SonarrConfig(**config["sonarr"])
            sonarr_config = config["sonarr"].copy()
            dynamic_load_config = sonarr_config.pop("dynamic_load", None)
            self.sonarr = SonarrConfig(**sonarr_config, dynamic_load=DynamicLoad(**dynamic_load_config))
            self.overseerr = OverseerrConfig(**config["overseerr"])
            self.plex = PlexConfig(**config["plex"])
            self.last_watched_deletion_threshold = self._convert_to_seconds(config["last_watched_deletion_threshold"], "last_watched_deletion_threshold")
            self.unwatched_deletion_threshold = self._convert_to_seconds(config["unwatched_deletion_threshold"], "unwatched_deletion_threshold")
            self.dry_run = config["dry_run"] in [True, "True", "true", "1"]
            self.schedule_interval = config["schedule_interval"]
        except KeyError as err:
            print("Error in configuration file:")
            print(err)
            sys.exit()
        except TypeError as err:
            print("Error in configuration file:")
            print(err)
            sys.exit()
        except ValueError as err:
            print("Error in configuration file:")
            print(err)
            sys.exit()
    
    def _convert_to_seconds(self, time: str, key_name: str) -> int:
        """
        Converts a time string to seconds.

        Args:
            time: A string representing a time interval in the format of <integer><unit>. (e.g. 45s (seconds), 30m (minutes), 2h (hours), 1d (days))

        Returns:
            An integer representing the time interval in seconds.

        Raises:
            ValueError: If the time string is not in the correct format or contains an invalid time unit.
        """
        # If the time is already an integer, return it
        try:
            return int(time)
        except:
            pass

        match = re.match(r'^(\d+)([smhd])$', time.lower())
        
        if not match:
            raise ValueError(f"{key_name} must be in the format of <integer><unit>. (e.g. 45s (seconds), 30m (minutes), 2h (hours), 1d (days))")
        
        value, unit = int(match.group(1)), match.group(2)

        if unit == "s":
            return value
        elif unit == "m":
            return value * 60
        elif unit == "h":
            return value * 3600
        elif unit == "d":
            return value * 86400
        else:
            raise ValueError(f"'retrieval_interval' invalid time unit: {unit}. Accepted units: s (seconds), m (minutes), h (hours), d (days).")