CONSUMERS = []


def register(consumer_cls):
    """
    Register a consumer class so it can be started automatically.
    """
    if consumer_cls not in CONSUMERS:
        CONSUMERS.append(consumer_cls)
    return consumer_cls  # optional, allows decorator usage
