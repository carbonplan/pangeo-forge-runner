import json
import subprocess
import tempfile
import time

import pytest
import xarray as xr


def test_dataflow_integration():
    bucket = "gs://pangeo-forge-runner-ci-testing"
    config = {
        "Bake": {
            "prune": True,
            "bakery_class": "pangeo_forge_runner.bakery.dataflow.DataflowBakery",
        },
        "DataflowBakery": {"temp_gcs_location": bucket + "/temp"},
        "TargetStorage": {
            "fsspec_class": "gcsfs.GCSFileSystem",
            "root_path": bucket + "/target/{job_name}",
        },
        "InputCacheStorage": {
            "fsspec_class": "gcsfs.GCSFileSystem",
            "root_path": bucket + "/input-cache/{job_name}",
        },
        "MetadataCacheStorage": {
            "fsspec_class": "gcsfs.GCSFileSystem",
            "root_path": bucket + "/metadata-cache/{job_name}",
        },
    }

    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        json.dump(config, f)
        f.flush()
        cmd = [
            "pangeo-forge-runner",
            "bake",
            "--repo",
            "https://github.com/pforgetest/gpcp-from-gcs-feedstock.git",
            "--ref",
            "0.9.x",
            "--json",
            "-f",
            f.name,
        ]
        print("\nSubmitting job...")
        submit_proc = subprocess.run(cmd, capture_output=True)
        assert submit_proc.returncode == 0
        lastline = json.loads(submit_proc.stdout.decode().splitlines()[-1])
        assert lastline["status"] == "submitted"
        job_id = lastline["job_id"]
        job_name = lastline["job_name"]
        print(f"Job submitted with {job_id = }")
        # note the start time, because certain errors on dataflow manifest as long hangs,
        # and if that's the case, we'll want to bail out of this test manually, rather than
        # wait for the the job to officially fail.
        start = time.time()

        # 6 minutes seems like an average runtime for these jobs, but being optimistic
        # let's start by waiting 5 minutes
        print(f"Waiting for 5 mins, starting at {start = }")
        time.sleep(60 * 5)

        # okay, time to start checking if the job is done
        show_job = f"gcloud dataflow jobs show {job_id} --format=json".split()
        while True:
            elapsed = time.time() - start
            print(f"Time {elapsed = }")
            if elapsed > 60 * 12:
                # if 12 minutes have elapsed (twice the expected time to complete the job),
                # we're going to assume the job is hanging, and call this test a failure.
                # remember: we're sourcing data for this job from within GCS, so networking
                # shouldn't delay things *too* much. if we eventually find that jobs may take
                # more than 12 minutes and not be hanging, we can change this assumption.
                pytest.fail(f"Time {elapsed = } exceedes 12 minutes.")

            # check job state
            state_proc = subprocess.run(show_job, capture_output=True)
            assert state_proc.returncode == 0
            state = json.loads(state_proc.stdout)["state"]
            print(f"Current {state = }")
            if state == "Done":
                # on Dataflow, "Done" means success
                break
            elif state == "Running":
                # still running, let's give it another 30s then check again
                time.sleep(30)
            else:
                # consider any other state a failure
                pytest.fail(f"{state = } is neither 'Done' nor 'Running'")

        # open the generated dataset with xarray!
        gpcp = xr.open_dataset(
            config["TargetStorage"]["root_path"].format(job_name=job_name),
            engine="zarr",
        )

        assert (
            gpcp.title
            == "Global Precipitation Climatatology Project (GPCP) Climate Data Record (CDR), Daily V1.3"
        )
        # --prune prunes to two time steps by default, so we expect 2 items here
        assert len(gpcp.precip) == 2
        print(gpcp)
