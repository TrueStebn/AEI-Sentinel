"""Windows kernel limits, installed before the child executes any Python code."""
import ctypes as C
from ctypes import wintypes as W
import os
import subprocess
import sys


def api():
    if os.name != 'nt':
        raise RuntimeError('The bounded deployment requires Windows Job Objects.')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([C.c_void_p, W.LPCWSTR], W.HANDLE),
        'SetInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        'QueryInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
        'AssignProcessToJobObject': ([W.HANDLE, W.HANDLE], W.BOOL),
        'IsProcessInJob': ([W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)], W.BOOL),
        'GetCurrentProcess': ([], W.HANDLE),
        'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.c_void_p, C.c_void_p], W.BOOL),
        'ResumeThread': ([W.HANDLE], W.DWORD),
        'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
        'GetExitCodeProcess': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
        'TerminateProcess': ([W.HANDLE, W.UINT], W.BOOL),
        'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
        'CloseHandle': ([W.HANDLE], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = args, result
    return kernel


class Basic(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64),
                ('flags', W.DWORD), ('min_ws', C.c_size_t), ('max_ws', C.c_size_t),
                ('active', W.DWORD), ('affinity', C.c_size_t),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]


class IO(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class Extended(C.Structure):
    _fields_ = [('basic', Basic), ('io', IO), ('process_memory', C.c_size_t),
                ('job_memory', C.c_size_t), ('peak_process', C.c_size_t), ('peak_job', C.c_size_t)]


class CPU(C.Structure):
    _fields_ = [('flags', W.DWORD), ('rate', W.DWORD)]


class Startup(C.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
                *[(name, W.DWORD) for name in ('x', 'y', 'xsize', 'ysize', 'xchars', 'ychars', 'fill', 'flags')],
                ('show', W.WORD), ('reserved_count', W.WORD), ('reserved_bytes', C.c_void_p),
                ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]


class Process(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]


FLAGS = 0x2000 | 0x200 | 0x8 | 0x400  # kill-on-close, job memory, active process, die on exception


def check(ok):
    if not ok:
        raise C.WinError(C.get_last_error())


def verify_current_job(memory_mb=2048):
    kernel = api()
    inside = W.BOOL()
    check(kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, C.byref(inside)))
    if not inside.value:
        raise RuntimeError('The deployment worker has no Windows Job Object.')
    limits, cpu = Extended(), CPU()
    check(kernel.QueryInformationJobObject(None, 9, C.byref(limits), C.sizeof(limits), None))
    check(kernel.QueryInformationJobObject(None, 15, C.byref(cpu), C.sizeof(cpu), None))
    if limits.basic.flags & FLAGS != FLAGS or limits.basic.active != 1 or not 0 < limits.job_memory <= memory_mb * 1024**2 or cpu.flags & 5 != 5 or not 0 < cpu.rate <= 2500:
        raise RuntimeError('Required kernel resource limits are missing.')
    return dict(memory_bytes=limits.job_memory, cpu_percent=cpu.rate / 100, processes=1)


def run_bounded(command, *, cwd, memory_mb=2048):
    """One owned suspended child, no restart, no breakaway, no persistent service."""
    kernel = api()
    job = kernel.CreateJobObjectW(None, None)
    check(job)
    child = Process()
    try:
        limits = Extended()
        limits.basic.flags, limits.basic.active = FLAGS, 1
        limits.job_memory = memory_mb * 1024**2
        check(kernel.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
        cpu = CPU(5, 2500)  # ENABLE | HARD_CAP, aggregate machine CPU
        check(kernel.SetInformationJobObject(job, 15, C.byref(cpu), C.sizeof(cpu)))
        startup = Startup()
        startup.cb = C.sizeof(startup)
        line = C.create_unicode_buffer(subprocess.list2cmdline(command))
        check(kernel.CreateProcessW(None, line, None, None, False, 0x4, None, str(cwd), C.byref(startup), C.byref(child)))
        # Suspended child cannot allocate/import/listen until assignment succeeds.
        check(kernel.AssignProcessToJobObject(job, child.process))
        if kernel.ResumeThread(child.thread) == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        while True:
            status = kernel.WaitForSingleObject(child.process, 500)
            if status == 0:
                code = W.DWORD()
                check(kernel.GetExitCodeProcess(child.process, C.byref(code)))
                return code.value
            if status != 258:
                raise C.WinError(C.get_last_error())
    finally:
        # Also handles assignment failure: kill the still-suspended owned child.
        if child.process:
            kernel.TerminateProcess(child.process, 130)
        kernel.TerminateJobObject(job, 130)
        for handle in (child.thread, child.process, job):
            if handle:
                kernel.CloseHandle(handle)


def main():
    from src.config import PROJECT_ROOT
    if sys.argv[1:] not in (['--local-check'], ['--isolated']):
        raise SystemExit('Use --local-check or --isolated. Neither command starts a tunnel.')
    isolated = sys.argv[1:] == ['--isolated']
    # Windows venv python.exe is a redirector which creates another process.
    # Invoke the real interpreter directly so the one-process kernel cap holds.
    import sysconfig
    if isolated:
        from src.demo_isolation import verify_isolation, prepare_environment, ROOT
        verify_isolation()
        print('Verified isolated environment:', prepare_environment(), flush=True)
        packages = str(ROOT / 'packages')
    else:
        packages = sysconfig.get_path('purelib')
    os.environ['AEI_ISOLATED_DEMO'] = '1' if isolated else '0'
    code = ('import sys,runpy;sys.path.insert(0,' + repr(packages)
            + ');runpy.run_module("tools.run_bounded_demo",run_name="__main__")')
    command = [sys._base_executable, '-B', '-c', code]
    try:
        raise SystemExit(run_bounded(command, cwd=PROJECT_ROOT))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == '__main__':
    main()
