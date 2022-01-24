import requests
from dataclasses import dataclass

WOLT_DOMAIN = 'restaurant-api.wolt.com'
WOLT_URL = f'https://{WOLT_DOMAIN}'
SEARCH_URL = f'{WOLT_URL}/v1/pages/search'
RESTAURANT_INFO_URL = f'{WOLT_URL}/v3/venues/slug/'


@dataclass(frozen=True)
class Restaurant:
    name: str
    slug: str

    @property
    def info_url(self):
    	return RESTAURANT_INFO_URL + self.slug


class WoltAPI(object):
    @staticmethod
    def lookup_restaurant(name):
        params = {
            "q": name,
            # Since 25.11.2021, Wolt's backend requires your location for searchs.
            # So heres Dizengoff Center for you...
            "lat": 32.075409,
            "lon": 34.775134
        }
        result = requests.get(SEARCH_URL, params=params).json()
        # Result always has a single section.
        section = result["sections"][0]

        if section["name"] == "no-content":
            # This means there are no search results
            return []

        restaurants = []
        for restaurant in section["items"]:
            restaurants.append(Restaurant(name=restaurant["title"], slug=restaurant["venue"]["slug"]))
        return restaurants