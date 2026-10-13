"""`make e2e`'s custody steps, in the harness `make e2e` started: OANDA in
sample data, its synthetic account (src/oanda/synthetic.py). SIMULATED ONLY:
nothing reaches OANDA, its practice host or its live one.

1. The plugin registers; sample data is set on.
2. Account links lists the synthetic account; the plugin's own form creates
   "E2E OANDA" and links the account to it.
3. The street holds the account's statement exactly as
   e2e/expected.street says: a row per currency, each open position as its
   two cash legs (rolling spot, ruled 2026-10-13), and the account's figures
   (its margin used). The harness's platform holds no spot-date rule, so no
   leg's value date can be worked out, and each row states no settled or
   pending split rather than a guessed date (the street takes no pending
   quantity without its date).
4. The street's activity is e2e/expected.activity: the account's history
   backfilled to its opening, one of each kind OANDA's transactions convert
   to, each once.
5. A row's raw-record reference resolves on the Raw records tab.
6. The plugin's container made again backfills again, recording nothing
   twice.

Standard library only; the Makefile hands it the harness's compose command
as its arguments.
"""

import difflib
import subprocess
import sys
import time

COMPOSE = sys.argv[1:]
LOG = ".e2e/custody.log"
ACCOUNT = "101-001-0000000-001"


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout}{done.stderr}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args):
    return compose("run", "--rm", "-T", "runner", *args).stdout


def store(what):
    return compose("run", "--rm", "-T", "store", what).stdout


def until(seconds, attempt, what):
    deadline = time.monotonic() + seconds
    while True:
        answer = attempt()
        if answer:
            return answer
        if time.monotonic() > deadline:
            raise Failed(f"{what}, in {seconds} seconds")
        time.sleep(2)


def same(expected_file, lines, what):
    with open(expected_file, encoding="utf-8") as held:
        expected = held.read().splitlines()
    with open(".e2e/found." + expected_file.rsplit(".", 1)[-1], "w", encoding="utf-8") as found:
        found.write("".join(line + "\n" for line in lines))
    if expected != lines:
        diff = "\n".join(difflib.unified_diff(expected, lines, expected_file, "found", lineterm=""))
        log(diff)
        raise Failed(f"{what} is not {expected_file}:\n{diff[-1500:]}")


def street_of(account):
    return sorted(line for line in store("street").splitlines() if f"|{account}|" in line)


def activity_of(account):
    return sorted(
        line for line in store("activity").splitlines() if line.startswith(f"activity|{account}|")
    )


def run():
    runner("ready")
    runner("settings", "sample_data=true")
    runner("page", "--level", "admin", "/admin/accounts", "--until", ACCOUNT)
    runner(
        "form", "--level", "admin", "--page", "/admin/accounts", "--post", "/admin/accounts/link",
        "intent=create", f"external_account_id={ACCOUNT}", "new_account_name=E2E OANDA",
        "--expect", "Created E2E OANDA and linked",
    )
    until(
        120,
        lambda: any(line.startswith("statement|E2E OANDA|oanda|") for line in street_of("E2E OANDA")),
        "step 3, the statement never reached the street",
    )
    statement = street_of("E2E OANDA")
    same("e2e/expected.street", statement, "step 3, the street")
    activity = until(
        120,
        lambda: (lambda found: found if len(found) >= EXPECTED_ACTIVITY else None)(
            activity_of("E2E OANDA")
        ),
        "step 4, the activity never reached the street",
    )
    same("e2e/expected.activity", activity, "step 4, the activity")
    runner("grant", "--level", "read", "--role", "custody")
    reference = until(
        60,
        lambda: next(
            (line.split("|")[-1] for line in compose(
                "exec", "-T", "oanda", "python", "-c",
                "from meridian import edge; from oanda.raw import RawStore;"
                f"r = RawStore(edge.storage_dir(), edge.storage_dir()).latest('responses', '{ACCOUNT}', 1);"
                f"print('|' + r[0].key + '/account' if r else '')",
            ).stdout.splitlines() if line.startswith("|")),
            None,
        ),
        "step 5, no read of the account was kept",
    )
    runner("page", "--level", "read", f"/raw?ref={reference}", "--until",
           "The record a row references")
    compose("up", "-d", "--no-deps", "--force-recreate", "oanda")
    runner("ready")
    until(
        120,
        lambda: "activities sent from" in compose("logs", "--no-color", "oanda").stdout.split(
            "registered as"
        )[-1],
        "step 6, the new container never read the account's history again",
    )
    same("e2e/expected.activity", activity_of("E2E OANDA"), "step 6, the activity again")
    print(
        f"custody OK: sample data on and E2E OANDA linked through Account links; the street "
        f"holds the statement as e2e/expected.street ({len(statement)} lines: a row per "
        f"currency of the open positions' two cash legs, no settled or pending split where the "
        f"harness's platform holds no spot-date rule to date them); the activity backfilled to the "
        f"account's opening as e2e/expected.activity ({len(activity)} activities), each once, "
        f"and again from a new container nothing twice; a row's raw record resolved"
    )


#: How many activities the synthetic account's history holds (synthetic.py).
EXPECTED_ACTIVITY = 12


def main():
    open(LOG, "w").close()
    try:
        run()
    except (Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError) as failed:
        print(f"custody FAILED: {failed}; the run is in {LOG}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
