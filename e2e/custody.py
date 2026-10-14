"""`make e2e`'s custody steps, in the harness `make e2e` started: OANDA in
sample data, its synthetic account (src/oanda/synthetic.py). SIMULATED ONLY:
nothing reaches OANDA, its practice host or its live one.

1. The plugin registers; sample data is set on.
2. The account is chosen (0.3.0): Connections lists the accounts the
   synthetic token reaches (synthetic.py answering OANDA's
   `GET /v3/accounts`), the v1 account unusable with why, and the one
   usable v20 account preselected -- but not chosen: nothing is read, and
   Account links has no account, until the admin, holding admin on both
   of the plugin's roles, confirms it with the page's own form, sending
   the account the form shows selected.
3. Account links lists the synthetic account; the plugin's own form creates
   "E2E OANDA" and links the account to it.
4. The street holds the account's statement exactly as
   e2e/expected.street says: a row per currency, each open position as its
   two cash legs (rolling spot, ruled 2026-10-13), and the account's figures
   (its margin used). The deployment holds no EUR/USD spot-date rule until
   this first statement's read misses it and it is pulled from the harness's
   platform, so no leg of the first statement can be dated, and each row
   states no settled or pending split rather than a guessed date (the street
   takes no pending quantity without its date). The order path (orders.py),
   later, reads the pulled rule: its fills pend to the date it gives.
5. The street's activity is e2e/expected.activity: the account's history
   backfilled to its opening, one of each kind OANDA's transactions convert
   to, each once.
6. A row's raw-record reference resolves on the Raw records tab.
7. The plugin's container made again backfills again, recording nothing
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
V1_ACCOUNT = "7000001"


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


def must(condition, why):
    if not condition:
        raise Failed(why)


def run():
    runner("ready")
    runner("settings", "sample_data=true")
    # 2. Listed and preselected, not chosen: the admin confirms it. Choosing
    # it needs admin on both roles, as the Account ID setting does.
    runner("grant", "--level", "admin", "--role", "connectivity")
    listed = runner("page", "--level", "admin", "/admin/connections", "--until",
                    "Use this account")
    must(f'<option value="{ACCOUNT}" selected>' in listed,
         f"step 2, {ACCOUNT} is not preselected: {listed[-1500:]}")
    must(f'<tr data-account="{V1_ACCOUNT}">' in listed and "a v1 account" in listed
         and f'<option value="{V1_ACCOUNT}"' not in listed,
         f"step 2, the v1 account is not listed unusable: {listed[-1500:]}")
    must("Waiting for the account" in listed and "No connections yet" in listed,
         f"step 2, a connection before the account was chosen: {listed[-1500:]}")
    unchosen = runner("page", "--level", "admin", "/admin/accounts")
    must("No account yet" in unchosen and ACCOUNT not in unchosen,
         f"step 2, the account was read before it was chosen: {unchosen[-1500:]}")
    runner(
        "form", "--level", "admin", "--page", "/admin/connections",
        "--post", "/admin/connections/choose-account", "--from-page", "account_id",
        "--expect", f"Using {ACCOUNT}: the connection synthetic:{ACCOUNT}, read-only",
    )
    # 3. Linked through Account links.
    runner("page", "--level", "admin", "/admin/accounts", "--until", ACCOUNT)
    runner(
        "form", "--level", "admin", "--page", "/admin/accounts", "--post", "/admin/accounts/link",
        "intent=create", f"external_account_id={ACCOUNT}", "new_account_name=E2E OANDA",
        "--expect", "Created E2E OANDA and linked",
    )
    until(
        120,
        lambda: any(line.startswith("statement|E2E OANDA|oanda|") for line in street_of("E2E OANDA")),
        "step 4, the statement never reached the street",
    )
    statement = street_of("E2E OANDA")
    same("e2e/expected.street", statement, "step 4, the street")
    activity = until(
        120,
        lambda: (lambda found: found if len(found) >= EXPECTED_ACTIVITY else None)(
            activity_of("E2E OANDA")
        ),
        "step 5, the activity never reached the street",
    )
    same("e2e/expected.activity", activity, "step 5, the activity")
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
        "step 6, no read of the account was kept",
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
        "step 7, the new container never read the account's history again",
    )
    same("e2e/expected.activity", activity_of("E2E OANDA"), "step 7, the activity again")
    print(
        f"custody OK: sample data on; the synthetic token's accounts listed on Connections, "
        f"the v1 account unusable and the v20 account preselected, read only once the admin "
        f"confirmed it; E2E OANDA linked through Account links; the street "
        f"holds the statement as e2e/expected.street ({len(statement)} lines: a row per "
        f"currency of the open positions' two cash legs, no settled or pending split before "
        f"the deployment has pulled the spot-date rule that dates them); the activity "
        f"backfilled to the account's opening as e2e/expected.activity ({len(activity)} "
        f"activities), each once, and again from a new container nothing twice; a row's raw "
        f"record resolved"
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
