"""Frozen Windows entry point for Service Dependency Mapper."""

from multiprocessing import freeze_support

from service_dependency_mapper.gui import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
