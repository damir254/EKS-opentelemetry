"""Exercise the release gate without Docker, AWS credentials or network access."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "publish-image.sh"
DIGEST = "sha256:" + "a" * 64


class ReleaseGateTests(unittest.TestCase):
    def run_publish(self, fail=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image.tar").write_bytes(b"fake OCI archive for command-order tests")
            (root / "docker").write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
stage = 'release' if args[-1].split(':')[-1].startswith('release-') else 'candidate'
log = pathlib.Path(os.environ['TEST_LOG'])
with log.open('a') as f: f.write(stage + '\\n')
if os.environ['FAIL_AT'] == stage: sys.exit(1)
digestfile = args[args.index('--digestfile') + 1]
digest = ('sha256:' + 'b' * 64) if os.environ['FAIL_AT'] == 'digest' else os.environ['EXPECTED_DIGEST']
(pathlib.Path(os.environ['OCI_ARCHIVE']).parent / pathlib.Path(digestfile).name).write_text(digest)
""")
            (root / "cosign").write_text("""#!/usr/bin/env python3
import os, pathlib, sys
step = sys.argv[1]
with pathlib.Path(os.environ['TEST_LOG']).open('a') as f: f.write(step + '\\n')
if os.environ['FAIL_AT'] == step: sys.exit(1)
""")
            for tool in ("docker", "cosign"):
                (root / tool).chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       OCI_ARCHIVE=str(root / "image.tar"), ECR_IMAGE="example.invalid/payment",
                       EXPECTED_DIGEST=DIGEST, SKOPEO_IMAGE="example.invalid/skopeo@" + DIGEST,
                       CERTIFICATE_IDENTITY="https://github.com/example/project/workflow@refs/heads/main",
                       GITHUB_SHA="c" * 40, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
                       TEST_LOG=str(root / "commands"), FAIL_AT=fail)
            result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            commands = (root / "commands").read_text().splitlines()
            return result.returncode, commands

    def test_release_is_last_after_signature_verification(self):
        code, commands = self.run_publish()
        self.assertEqual(code, 0)
        self.assertEqual(commands, ["candidate", "sign", "verify", "release"])

    def test_no_release_on_copy_digest_sign_or_verification_failure(self):
        for failure in ("candidate", "digest", "sign", "verify"):
            with self.subTest(failure=failure):
                code, commands = self.run_publish(failure)
                self.assertNotEqual(code, 0)
                self.assertNotIn("release", commands)

    def test_release_copy_failure_fails_ci(self):
        code, commands = self.run_publish("release")
        self.assertNotEqual(code, 0)
        self.assertEqual(commands, ["candidate", "sign", "verify", "release"])


if __name__ == "__main__":
    unittest.main()
