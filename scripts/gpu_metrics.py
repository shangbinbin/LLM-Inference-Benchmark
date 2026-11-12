"""Native GPU metric contract shared by readiness, relabeling and dashboards."""
DEVICE_METRICS = {'nvidia': 'DCGM_FI_DEV_GPU_UTIL', 'hygon': 'dcu_memorycap_bytes'}
GPU_JOBS = {'nvidia': 'nvidia-dcgm-exporter', 'hygon': 'hygon-dcu-exporter'}


def exporter_port(cfg, vendor):
    # Project default; an existing exporter can listen on a different port.
    return 9400 if vendor == 'nvidia' else cfg['gpu'].get('hygon_exporter_port', 16080)
