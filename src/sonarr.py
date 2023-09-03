import requests
from src.util import convert_bytes
from src.models.sonarr.sonarrseries import SonarrSeries

class SonarrClient:
    def __init__(self, config):
        self.config = config
        self.api_key = config.sonarr.api_key
        self.base_url = config.sonarr.base_url
        self.monitor_continuing_series = config.sonarr.monitor_continuing_series
        self.keep_pilot_episodes = config.sonarr.keep_pilot_episodes
        self.exempt_tag_names = config.sonarr.exempt_tag_names
        self.dry_run = config.dry_run

    def get_series(self):
        """
        Retrieves all series from Sonarr.

        Returns:
            dict: A dictionary containing the series ID as the key and the series title as the value.

        Raises:
            requests.exceptions.RequestException: If the request to retrieve the series fails.
        """
        url = f"{self.base_url}/series"
        headers = {"X-Api-Key": self.api_key}
        exempt_tag_ids = self.get_sonarr_tag_ids(self.exempt_tag_names) if self.exempt_tag_names else []

        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            series = []
            series_data = response.json()
            for show in series_data:
                series_obj = self.parse_series(show, exempt_tag_ids)
                series.append(series_obj)
            return series

        raise requests.exceptions.RequestException(f"Fetching series failed with status code {response.status_code}")

    def parse_series(self, series, exempt_tag_ids):
        """
        Parses a Sonarr series object into a SonarrSeries object.

        Args:
            series (dict): The Sonarr series object to parse.

        Returns:
            SonarrSeries: A SonarrSeries object representing the series.
        """
        tvdb_id = series["tvdbId"] if "tvdbId" in series else None
        imdb_id = series["imdbId"] if "imdbId" in series else None
        path = series["path"] if "path" in series else None
        title = series["title"] if "title" in series else None
        status = series["status"] if "status" in series else None
        tags = series["tags"] if "tags" in series else []
        exempt = any(tag in exempt_tag_ids for tag in tags) if exempt_tag_ids else False

        return SonarrSeries(tvdb_id, imdb_id, path, title, status, tags, exempt)
    
    def get_sonarr_tag_ids(self, tag_names):
        """
        Retrieves the Sonarr tag ID given its name.

        Args:
            tag_name (str): The name of the tag.

        Returns:
            int[]: The IDs of the exempted tag names.

        Raises:
            requests.exceptions.RequestException: If the request to retrieve the Sonarr tag ID fails.
        """
        url = f"{self.base_url}/tag"
        headers = {"X-Api-Key": self.api_key}

        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            tag_ids = []
            tags = response.json()
            for tag in tags:
                if tag["label"] in tag_names:
                    tag_ids.append(tag["id"])
            return tag_ids
        else:
            raise requests.exceptions.RequestException(
                f"Fetching Sonarr tag ID failed with status code {response.status_code}"
            )

    def get_sonarr_item(self, tvdb_id):
        """
        Retrieves the Sonarr ID, title, and size on disk for a TV series with the given TVDB ID.

        Args:
            tvdb_id (int): The TVDB ID of the TV series.

        Returns:
            tuple: A tuple containing the Sonarr ID (int), title (str), and size on disk (int) of the TV series.

        Raises:
            requests.exceptions.RequestException: If the request to retrieve the Sonarr ID fails.
        """
        url = f"{self.base_url}/series"
        headers = {"X-Api-Key": self.api_key}
        params = {"tvdbId": tvdb_id}

        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            series = response.json()
            if not series:
                return None

            return series[0]

        raise requests.exceptions.RequestException(f"Fetching Sonarr ID failed with status code {response.status_code}")

    def find_and_delete_series(self, tvdb_id):
        """
        Finds a TV series in Sonarr by its TVDB ID and deletes it along with its files.

        Args:
            tvdb_id (int): The TVDB ID of the TV series.

        Returns:
            Tuple[boolean, int]: A tuple containing whether the series was deleted or unmonitored and the size of the disk space freed up by deleting the TV series.

        Raises:
            requests.exceptions.RequestException: If the request to retrieve the Sonarr ID fails or if the deletion fails.
        """
        exempt_tag_ids = self.get_sonarr_tag_ids(self.exempt_tag_names)
        series = self.get_sonarr_item(tvdb_id)

        if series is None:
            raise requests.exceptions.RequestException("Fetching Sonarr ID failed")

        title = series["title"]
        size_on_disk = series["statistics"]["sizeOnDisk"]
        ended = series["ended"]
        tags = series["tags"]

        if exempt_tag_ids is not None and tags is not None and any(tag in exempt_tag_ids for tag in tags):
            print(f"SONARR :: {title} is exempt from deletion")
            return False, 0

        deleted = False
        if ended or not self.monitor_continuing_series:
            deleted = self.delete_unplayed_series(series)
        else:
            size_on_disk = 0
            deleted = self.unmonitor_unplayed_series(series)
            if deleted:
                series = self.get_sonarr_item(tvdb_id)
                if series is None:
                    raise requests.exceptions.RequestException("Fetching Sonarr ID failed")

                has_unmonitored_episodes = False
                for season in series["seasons"]:
                    if season["monitored"] is False and season["statistics"]["episodeFileCount"] > 0:
                        has_unmonitored_episodes = True
                        break

                if has_unmonitored_episodes:
                    size_on_disk = self.delete_unmonitored_episodes(series)
                else:
                    deleted = False

        return deleted, size_on_disk

    def delete_unplayed_series(self, series):
        """
        Deletes a TV series in Sonarr by its Sonarr ID and deletes its files.

        Args:
            sonarr_id (int): The Sonarr ID of the TV series.
            title (str): The title of the TV series.
            size_on_disk (int): The size on disk of the TV series.

        Returns:
            boolean: Whether the series was deleted.

        Raises:
            Exception: If the deletion fails.
        """
        sonarr_id = series["id"]
        title = series["title"]
        size_on_disk = series["statistics"]["sizeOnDisk"]

        url = f"{self.base_url}/series/{sonarr_id}"
        headers = {"X-Api-Key": self.api_key}
        params = {"deleteFiles": "true"}

        if self.dry_run:
            print(f"SONARR :: DRY RUN :: {title} would be deleted. {convert_bytes(size_on_disk)} would be freed up")
            return True

        response = requests.delete(url, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            print(f"SONARR :: {title} deleted successfully. {convert_bytes(size_on_disk)} freed up")
            return True

        raise requests.exceptions.RequestException(f"Deletion failed with status code {response.status_code}")

    def delete_unmonitored_episodes(self, series):
        """
        Deletes unmonitored episodes of a TV series in Sonarr by its Sonarr ID.

        Args:
            series (dict): A dictionary containing information about the TV series.

        Returns:
            int: The total size on disk of the deleted episodes.

        Raises:
            requests.exceptions.RequestException: If the deletion fails.
        """
        unmonitored_seasons = []
        for season in series["seasons"]:
            if not season["monitored"]:
                unmonitored_seasons.append(season["seasonNumber"])

        title = series["title"]
        episodes_deleted_count = 0
        size_on_disk = 0

        url = f"{self.base_url}/episodefile"
        headers = {"X-Api-Key": self.api_key}
        params = {"seriesId": series["id"]}

        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            episodes = response.json()
            if not episodes:
                raise requests.exceptions.RequestException("Fetching Sonarr episodes failed")
            
            matches = ["s01e01", "s1e1"]

            for episode in episodes:
                if episode["seasonNumber"] == 1 and any(x in episode["relativePath"].lower() for x in matches) and self.keep_pilot_episodes:
                    continue
                if episode["seasonNumber"] not in unmonitored_seasons:
                    continue

                if self.dry_run:
                    size_on_disk += episode["size"]
                    episodes_deleted_count += 1
                    print(
                        f"SONARR :: DRY RUN :: {title} S{episode['seasonNumber']:02}E{episode['episodeNumber']:02} would be deleted. {convert_bytes(episode['size'])} would be freed up"
                    )
                    continue

                url = f"{self.base_url}/episodefile/{episode['id']}"

                response = requests.delete(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    size_on_disk += episode["size"]
                    episodes_deleted_count += 1
                else:
                    raise requests.exceptions.RequestException(
                        f"Deletion failed with status code {response.status_code}"
                    )
        else:
            raise requests.exceptions.RequestException(
                f"Fetching episodes failed with status code {response.status_code}"
            )

        if self.dry_run:
            print(
                f"SONARR :: DRY RUN :: {title} would have had {episodes_deleted_count} unmonitored episodes deleted. {convert_bytes(size_on_disk)} would be freed up"
            )
        else:
            print(
                f"SONARR :: {title} had {episodes_deleted_count} unmonitored episodes deleted. {convert_bytes(size_on_disk)} freed up"
            )

        return size_on_disk

    def unmonitor_unplayed_series(self, series):
        """
        Unmonitors a TV series in Sonarr by its Sonarr ID.
        
        Args:
            series (dict): A dictionary containing information about the TV series.
            
        Returns:
            boolean: Whether the series was unmonitored.

        Raises:
            requests.exceptions.RequestException: If the unmonitoring fails.
        """
        series_id = series["id"]
        url = f"{self.base_url}/series/{series_id}"
        headers = {"X-Api-Key": self.api_key}
        title = series["title"]
        changed = False

        for season in series["seasons"]:
            if "nextAiring" in season["statistics"]:
                continue

            if season["monitored"]:
                season["monitored"] = False
                changed = True

        if self.dry_run and changed:
            print(f"SONARR :: DRY RUN :: {title} would be unmonitored")
            return changed

        response = requests.put(url, headers=headers, timeout=30, json=series)
        if response.status_code == 202:
            if changed:
                print(f"SONARR :: {title} unmonitored successfully")
            return changed

        raise requests.exceptions.RequestException(f"Unmonitoring {title} failed with status code {response.status_code}")
