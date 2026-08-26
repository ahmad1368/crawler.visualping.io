from app.events import CRAWL_FINISHED, MATCH_FOUND, PAGE_FETCHED, EventBus


def test_subscriber_receives_events_in_order():
    bus = EventBus()
    received = []
    bus.subscribe(PAGE_FETCHED, received.append)

    bus.publish(PAGE_FETCHED, "url1")
    bus.publish(PAGE_FETCHED, "url2")
    bus.publish(PAGE_FETCHED, "url3")

    assert received == ["url1", "url2", "url3"]


def test_multiple_subscribers_all_receive_the_event():
    bus = EventBus()
    received_a = []
    received_b = []
    bus.subscribe(MATCH_FOUND, received_a.append)
    bus.subscribe(MATCH_FOUND, received_b.append)

    bus.publish(MATCH_FOUND, "match-1")

    assert received_a == ["match-1"]
    assert received_b == ["match-1"]


def test_subscribers_only_receive_their_own_event_type():
    bus = EventBus()
    page_events = []
    crawl_events = []
    bus.subscribe(PAGE_FETCHED, page_events.append)
    bus.subscribe(CRAWL_FINISHED, crawl_events.append)

    bus.publish(PAGE_FETCHED, "url1")
    bus.publish(CRAWL_FINISHED, "summary")

    assert page_events == ["url1"]
    assert crawl_events == ["summary"]


def test_publish_with_no_subscribers_does_not_raise():
    bus = EventBus()

    bus.publish(PAGE_FETCHED, "url1")


def test_subscribing_later_only_receives_future_events():
    bus = EventBus()
    bus.publish(PAGE_FETCHED, "before-subscribing")

    received = []
    bus.subscribe(PAGE_FETCHED, received.append)
    bus.publish(PAGE_FETCHED, "after-subscribing")

    assert received == ["after-subscribing"]
