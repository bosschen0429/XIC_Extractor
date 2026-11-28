from xic_extractor.configuration.execution_profile import ExecutionProfile


def in_process_execution() -> ExecutionProfile:
    """Run everything in the calling process, for injected readers."""
    return ExecutionProfile(backend="thread")
