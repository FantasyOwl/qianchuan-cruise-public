if __name__ == "__main__":
    from monitor.parent_guard import start_parent_guard
    guard=start_parent_guard()
    try:
        from monitor.server import main
        main()
    finally:
        if guard is not None:
            guard.close()
