# Test cases for ybcal.yaml: `semgrep --test contrib/semgrep/rules` (contrib/semgrep/README.md).
# Not part of ybcal; listed in .semgrepignore so the scan itself never reports it.
import os
import pickle
import subprocess
import urllib.request

import numpy as np
import requests
import yaml

# ruleid: ybcal.data.network-fetch-without-provenance
urllib.request.urlopen("https://example.invalid/prices.csv")
# ruleid: ybcal.data.network-fetch-without-provenance
requests.get("https://example.invalid/prices.csv")

# ruleid: ybcal.data.unsafe-deserialization
pickle.load(open("run.pkl", "rb"))
# ruleid: ybcal.data.unsafe-deserialization
yaml.load(open("scenario.yaml"))
# ok: ybcal.data.unsafe-deserialization
yaml.load(open("scenario.yaml"), Loader=yaml.SafeLoader)
# ok: ybcal.data.unsafe-deserialization
yaml.safe_load(open("scenario.yaml"))
# ruleid: ybcal.data.unsafe-deserialization
np.load("paths.npz", allow_pickle=True)
# ok: ybcal.data.unsafe-deserialization
np.load("paths.npz", allow_pickle=False)

# ruleid: ybcal.subprocess.shell
subprocess.run("git rev-parse HEAD", shell=True)
# ruleid: ybcal.subprocess.shell
os.system("git rev-parse HEAD")
# ok: ybcal.subprocess.shell
subprocess.run(["git", "rev-parse", "HEAD"], check=True)
