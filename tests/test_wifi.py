from huckdeck.setup.wifi import _friendly_join_error, parse_scan, split_terse


def test_split_terse_unescapes_colons_and_backslashes():
    assert split_terse(r"Cafe\: Guest:54:WPA2") == ["Cafe: Guest", "54", "WPA2"]
    assert split_terse(r"a\\b:1:") == ["a\\b", "1", ""]


def test_parse_scan_dedups_sorts_and_drops_hidden():
    out = "Home:61:WPA2\nHome:88:WPA2\nOpen:54:\n:70:WPA2\nFar:20:WPA2 WPA3\n"
    nets = parse_scan(out)
    assert [n.ssid for n in nets] == ["Home", "Open", "Far"]
    assert nets[0].signal == 88 and nets[0].secured
    assert not nets[1].secured


def test_friendly_join_error():
    assert "password" in _friendly_join_error("Secrets were required, but not provided")
    assert "Timed out" in _friendly_join_error("Connection activation failed: timeout")
    assert _friendly_join_error("") == "Couldn't join the network."
