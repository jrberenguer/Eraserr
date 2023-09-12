import requests
from src.logger import logger
from src.util import convert_bytes

class SonarrClient:
    def __init__(self, config):
        self.config = config
        self.api_key = config.sonarr.api_key
        self.base_url = config.sonarr.base_url
        self.monitor_continuing_series = config.sonarr.monitor_continuing_series
        self.exempt_tag_names = config.sonarr.exempt_tag_names
        self.dynamic_load = config.sonarr.dynamic_load

    def __get_media(self):
        url = f"{self.base_url}/series"
        headers = {"X-Api-Key": self.api_key}

        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
            
        return response.json()

    def __get_exempt_tag_ids(self, tag_names: list):
        url = f"{self.base_url}/tag"
        headers = {"X-Api-Key": self.api_key}

        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
        
        tags = response.json()
        tag_ids = [tag["id"] for tag in tags if tag["label"] in tag_names]

        return tag_ids
    
    def __get_media_episodes(self, media_id: int):
        url = f"{self.base_url}/episode"
        headers = {"X-Api-Key": self.api_key}
        params = {"seriesId": media_id}

        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
        
        return response.json()
    
    def __put_media(self, series):
        url = f"{self.base_url}/series/{series.get('id')}"
        headers = {"X-Api-Key": self.api_key}

        response = requests.put(url, headers=headers, json=series, timeout=30)
        if response.status_code != 202:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
        
        return response.json()
        
    def __monitor_media_episodes(self, episode_ids: list, monitored: bool = False):
        url = f"{self.base_url}/episode/monitor"
        headers = {"X-Api-Key": self.api_key}
        body = {"episodeIds": episode_ids, "monitored": monitored}

        response = requests.put(url, headers=headers, json=body, timeout=30)
        if response.status_code != 202:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")

    def __unmonitor_empty_seasons(self, series):
        for season in series.get("seasons", []):
            if season.get("statistics", {}).get("episodeCount", 0) > 0:
                continue

            if season.get("monitored", False):
                season["monitored"] = False

        return series
    
    def __delete_media(self, media_id: int):
        url = f"{self.base_url}/series/{media_id}"
        headers = {"X-Api-Key": self.api_key}
        params = {"deleteFiles": True, "addImportListExclusion": False}

        response = requests.delete(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
        
    def __delete_media_episodes(self, episode_ids: list):
        url = f"{self.base_url}/episodefile/bulk"
        headers = {"X-Api-Key": self.api_key}
        body = {"episodeFileIds": episode_ids}

        response = requests.delete(url, headers=headers, json=body, timeout=60)
        if response.status_code != 200:
            raise requests.exceptions.RequestException(f"{response.url} : {response.status_code} - {response.text}")
        
    def __handle_ended_series(self, series, dry_run: bool = False):
        size_on_disk = series.get("statistics", {}).get("sizeOnDisk", 0)
        if dry_run:
            logger.info("[SONARR][DRY RUN] Would have deleted %s. Freed: %s.", series.get("title"), convert_bytes(size_on_disk))
            return size_on_disk
        
        try:
            self.__delete_media(series.get("id"))
            logger.info("[SONARR] Deleted %s. Freed: %s.", series.get("title"), convert_bytes(size_on_disk))
        except requests.exceptions.RequestException as err:
            logger.error("[SONARR] Failed to delete %s. Error: %s", series.get("title"), err)

        return series.get("statistics", {}).get("sizeOnDisk", 0)
    
    def __handle_continuing_series(self, series, dry_run: bool = False):
        episodes = self.__get_media_episodes(series.get("id"))
        filtered_episodes = [episode for episode in episodes if episode['seasonNumber'] != 0]
        sorted_episodes = sorted(filtered_episodes, key=lambda x: (x['seasonNumber'], x['episodeNumber']))
        episodes_to_unload = sorted_episodes[self.dynamic_load.episodes_to_load:]

        unmonitor_episode_ids = []
        delete_episode_file_ids = []

        for episode in episodes_to_unload:
            if episode.get("monitored", False):
                unmonitor_episode_ids.append(episode.get("id"))
            if episode.get("hasFile", False) and episode.get("episodeFileId") not in delete_episode_file_ids:
                delete_episode_file_ids.append(episode.get("episodeFileId"))

        size_on_disk = 0
            
        if dry_run:
            logger.info("[SONARR][DRY RUN] Would have unmonitored %s.", series.get("title"))
            return size_on_disk
        
        try:
            if unmonitor_episode_ids:
                self.__monitor_media_episodes(unmonitor_episode_ids, False)
                logger.info("[SONARR] Unmonitored %s.", series.get("title"))
            if delete_episode_file_ids:
                self.__delete_media_episodes(delete_episode_file_ids)
                original_size_on_disk = series.get("statistics", {}).get("sizeOnDisk", 0)
                series = self.__unmonitor_empty_seasons(series)
                series = self.__put_media(series)
                size_on_disk = original_size_on_disk - series.get("statistics", {}).get("sizeOnDisk", 0)
                
        except requests.exceptions.RequestException as err:
            logger.error("[SONARR] Failed to unmonitor %s. Error: %s", series.get("title"), err)
            return size_on_disk

        return size_on_disk
      
    def get_and_delete_media(self, media_to_delete: dict, dry_run: bool = False):
        """
        Gets and deletes media with the given ID from the Sonarr API.
        
        Args:
            media_to_delete: A dictionary where the key is the ID of the media to delete and the value is the title of the media.
            dry_run: Whether to perform a dry run.
            
        Returns:
            None.
            
        Raises:
            requests.exceptions.RequestException: If the API request fails.
        """
        media = self.__get_media()
        exempt_tag_ids = self.__get_exempt_tag_ids(self.exempt_tag_names)

        total_size = 0

        for series in media:
            if str(series.get("tvdbId")) not in media_to_delete.keys():
                continue

            if any(tag in exempt_tag_ids for tag in series.get("tags", [])):
                media_to_delete.pop(str(series.get("tvdbId")))
                logger.info("[SONARR] Skipping %s because it is exempt.", series.get("title"))
                continue

            if series.get("id") is not None:
                ended = series.get("ended", False)
                if not self.monitor_continuing_series or ended:
                    total_size += self.__handle_ended_series(series, dry_run)
                else:
                    total_size += self.__handle_continuing_series(series, dry_run)

        if dry_run:
            logger.info("[SONARR][DRY RUN] Would have total freed: %s.", convert_bytes(total_size))
        elif total_size > 0:
            logger.info("[SONARR] Total freed: %s.", convert_bytes(total_size))

        return media_to_delete

    def get_and_delete_media_episodes(self, media_to_delete: dict, dry_run: bool = False):
        media = self.__get_media()
        exempt_tag_ids = self.__get_exempt_tag_ids(self.exempt_tag_names)

        for series in media:
            if str(series.get("tvdbId")) not in media_to_delete.keys():
                continue

            if any(tag in exempt_tag_ids for tag in series.get("tags", [])):
                media_to_delete.pop(str(series.get("tvdbId")))
                logger.info("[SONARR] Skipping %s because it is exempt.", series.get("title"))
                continue
        return
    # def get_sonarr_episodes_by_series(self, series_id):
    #     """
    #     Retrieves the Sonarr ID, title, and size on disk for a TV series with the given TVDB ID.

    #     Args:
    #         tvdb_id (int): The TVDB ID of the TV series.
    #         season (int): The season number of the TV series.

    #     Returns:
    #         tuple: A tuple containing the Sonarr ID (int), title (str), and size on disk (int) of the TV series.

    #     Raises:
    #         requests.exceptions.RequestException: If the request to retrieve the Sonarr ID fails.
    #     """
    #     url = f"{self.base_url}/episode"
    #     headers = {"X-Api-Key": self.api_key}
    #     params = {"seriesId": series_id}

    #     response = requests.get(url, headers=headers, params=params, timeout=30)
    #     if response.status_code == 200:
    #         episodes = response.json()
    #         if not episodes:
    #             return None

    #         return episodes

    #     raise requests.exceptions.RequestException(f"Fetching Sonarr ID failed with status code {response.status_code}")
    
    # def monitor_episodes_by_id(self, episode_ids, monitored):
    #     url = f"{self.base_url}/episode/monitor"
    #     headers = {"X-Api-Key": self.api_key}
    #     body = {"episodeIds": episode_ids, "monitored": monitored}

    #     response = requests.put(url, headers=headers, json=body, timeout=30)
    #     if response.status_code == 202:
    #         episodes = response.json()
    #         if not episodes:
    #             return None

    #         return episodes

    #     raise requests.exceptions.RequestException(f"Fetching Sonarr ID failed with status code {response.status_code}")
    
    # def delete_episodes_by_id(self, episode_file_ids):
    #     url = f"{self.base_url}/episodefile/bulk"
    #     headers = {"X-Api-Key": self.api_key}
    #     body = {"episodeFileIds": episode_file_ids}

    #     response = requests.delete(url, headers=headers, json=body, timeout=60)
    #     if response.status_code == 200:
    #         episodes = response.json()
    #         if not episodes:
    #             return None

    #         return episodes
    
    # def search_episodes_by_id(self, episode_ids):
    #     url = f"{self.base_url}/command"
    #     headers = {"X-Api-Key": self.api_key}
    #     body = {"name": "EpisodeSearch", "episodeIds": episode_ids}

    #     response = requests.post(url, headers=headers, json=body, timeout=30)
    #     if response.status_code == 201:
    #         episodes = response.json()
    #         if not episodes:
    #             return None

    #         return episodes
    
    # def find_and_load_episodes(self, tvdb_id, season, episode):
    #     exempt_tag_ids = self.get_sonarr_tag_ids(self.exempt_tag_names)
    #     series = self.get_sonarr_item(tvdb_id)

    #     title = series["title"]
    #     tags = series["tags"]

    #     if exempt_tag_ids is not None and tags is not None and any(tag in exempt_tag_ids for tag in tags):
    #         print(f"SONARR :: {title} is exempt from dynamic load deletion")
    #         return False, 0

    #     if series is None:
    #         raise requests.exceptions.RequestException("Fetching Sonarr ID failed")
        
    #     return self.load_and_unload_episodes(series, season, episode)
    
    # def load_and_unload_episodes(self, series, season_number, episode_number):
    #     episode_count = 0
    #     episodes = self.get_sonarr_episodes_by_series(series["id"])
    #     filtered_episodes = [episode for episode in episodes if episode['seasonNumber'] != 0]
    #     sorted_episodes = sorted(filtered_episodes, key=lambda x: (x['seasonNumber'], x['episodeNumber']))
    #     episode_index = next((index for (index, episode) in enumerate(sorted_episodes) if episode['seasonNumber'] == season_number and episode['episodeNumber'] == episode_number), None)
    #     if episode_index is not None:
    #         episodes_to_load = sorted_episodes[episode_index+1:episode_index+self.dynamic_load.episodes_to_load+1]
    #         episodes_to_unload = sorted_episodes[self.dynamic_load.episodes_to_load:episode_index-self.dynamic_load.episodes_to_load]

    #         monitor_episode_ids = []
    #         search_episode_ids = []
    #         for episode in episodes_to_load:
    #             if not episode["monitored"]:
    #                 monitor_episode_ids.append(episode["id"])
    #             if not episode["hasFile"]:
    #                 print(f"SONARR :: Loading S{episode['seasonNumber']:02}E{episode['episodeNumber']:02} of {series['title']}")
    #                 episode_count += 1
    #                 search_episode_ids.append(episode["id"])

    #         if monitor_episode_ids:
    #             self.monitor_episodes_by_id(monitor_episode_ids, True)
    #         if search_episode_ids:
    #             self.search_episodes_by_id(search_episode_ids)

    #         unmonitor_episode_ids = []
    #         delete_episode_ids = []
    #         for episode in episodes_to_unload:
    #             if episode["monitored"]:
    #                 unmonitor_episode_ids.append(episode["id"])
    #             if episode["hasFile"]:
    #                 print(f"SONARR :: Unloading S{episode['seasonNumber']:02}E{episode['episodeNumber']:02} of {series['title']}")
    #                 if episode["episodeFileId"] not in delete_episode_ids:
    #                     delete_episode_ids.append(episode["episodeFileId"])

    #         if unmonitor_episode_ids:
    #             self.monitor_episodes_by_id(unmonitor_episode_ids, False)
    #         if delete_episode_ids:
    #             self.delete_episodes_by_id(delete_episode_ids)

    #     return episode_count