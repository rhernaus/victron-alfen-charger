from dbus.mainloop.glib import DBusGMainLoop

from alfen_driver.driver import AlfenDriver


def main() -> None:
    """
    Entry point: Set up D-Bus main loop and run the driver.
    """
    DBusGMainLoop(set_as_default=True)
    driver = AlfenDriver()
    driver.run()


if __name__ == "__main__":
    main()
