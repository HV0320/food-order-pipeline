TRANSITIONS_BY_EVENT = {
    "ORDER_PLACED": {
        "expected_status": "PLACED",
        "next_status": "CONFIRMED",
        "record_event_type": "ORDER_CONFIRMED",
        "next_event_type": "ORDER_CONFIRMED",
    },
    "ORDER_CONFIRMED": {
        "expected_status": "CONFIRMED",
        "next_status": "PREPARING",
        "record_event_type": "ORDER_PREPARING",
        "next_event_type": "ORDER_PREPARING",
    },
    "ORDER_PREPARING": {
        "expected_status": "PREPARING",
        "next_status": "READY",
        "record_event_type": "ORDER_READY",
        "next_event_type": "ORDER_READY",
    },
    "ORDER_READY": {
        "expected_status": "READY",
        "next_status": "OUT_FOR_DELIVERY",
        "record_event_type": "ORDER_OUT_FOR_DELIVERY",
        "next_event_type": "ORDER_OUT_FOR_DELIVERY",
    },
    "ORDER_OUT_FOR_DELIVERY": {
        "expected_status": "OUT_FOR_DELIVERY",
        "next_status": "DELIVERED",
        "record_event_type": "ORDER_DELIVERED",
        "next_event_type": None,
    },
}


TERMINAL_STATUSES = {"DELIVERED", "CANCELLED", "FAILED"}


def get_transition(event_type: str):
    return TRANSITIONS_BY_EVENT.get(event_type)
