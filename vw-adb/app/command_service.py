# SPDX-License-Identifier: GPL-3.0-or-later
from command_dispatcher import dispatch_command
from job_queue import PRIORITY_COMMAND


def submit_command(
    jobs,
    command,
    vin,
    value=None,
    spin_file=None,
):
    """
    Reiht einen schreibenden VW-Befehl mit höchster Priorität
    in den einzigen UI-Worker ein.

    Laufende abbrechbare Hintergrundjobs bekommen automatisch
    ihr Cancel-Signal über UIJobQueue.submit().
    """
    name = f"command:{command}:{vin}"

    return jobs.submit(
        name,
        dispatch_command,
        command,
        vin,
        value,
        spin_file,
        priority=PRIORITY_COMMAND,
        cancellable=False,
    )
