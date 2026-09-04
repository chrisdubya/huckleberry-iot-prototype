"""First-boot setup mode: hotspot + captive-portal web page for Wi-Fi.

With no saved Wi-Fi network the device can't reach Huckleberry, and it has
no screen or keyboard. So it brings up its own WPA2 hotspot (name and
password on the sticker), serves a small web page that lists nearby
networks, and joins the one the user picks. See flow.py for the sequence.
"""
