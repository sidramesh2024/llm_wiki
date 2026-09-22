"""Resource names for the underwriting Vertex AI Search stores."""

PROJECT_ID = "gcpexplore-487204"
SEARCH_LOCATION = "global"
COLLECTION = "default_collection"
AGENT_LOCATION = "us-central1"

DATA_STORES = {
    "accounts": "uw-accounts",
    "guidelines": "uw-guidelines",
    "exposures": "uw-exposures",
}

ENGINE_ID = "uw-search"


def data_store_name(store_id: str) -> str:
    return (
        f"projects/{PROJECT_ID}/locations/{SEARCH_LOCATION}"
        f"/collections/{COLLECTION}/dataStores/{store_id}"
    )


def serving_config(store_id: str) -> str:
    return f"{data_store_name(store_id)}/servingConfigs/default_config"
