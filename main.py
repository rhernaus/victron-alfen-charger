#!/usr/bin/env python3

from dbus.mainloop.glib import DBusGMainLoop

from alfen_driver.driver import AlfenDriver
from alfen_driver.web import start_web_server


def main() -> None:
    """
    Entry point: Set up D-Bus main loop and run the driver with web server.
    """
    DBusGMainLoop(set_as_default=True)
    driver = AlfenDriver()
    start_web_server(driver)
    driver.run()


if __name__ == "__main__":
    main()
