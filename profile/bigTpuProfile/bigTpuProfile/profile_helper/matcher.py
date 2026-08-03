"""PMU and command stream matching helpers."""


def get_command_type(command):
    task_type = -1
    eu_type = -1
    if hasattr(command, "reg"):
        if hasattr(command.reg, "tsk_typ"):
            task_type = command.reg.tsk_typ
            eu_type = command.reg.tsk_eu_typ
        if hasattr(command.reg, "cmd_type"):
            task_type = command.reg.cmd_type
            eu_type = command.reg.cmd_special_function
    else:
        task_type = command.des_tsk_typ
        eu_type = command.des_tsk_eu_typ
    return task_type, eu_type


def copy_command_tags(record, *sources, context=True, mode=True):
    for source in sources:
        if source is None:
            continue
        if context and hasattr(source, "func_name"):
            record.func_name = source.func_name
        if context and hasattr(source, "func_scope_id"):
            record.func_scope_id = source.func_scope_id
        if context and hasattr(source, "batch_idx"):
            record.batch_idx = source.batch_idx
        if mode and hasattr(source, "mode"):
            record.mode = source.mode


def is_system_monitor(record):
    if (
        hasattr(record, "axi_d0_wr_vaild_cntr")
        and record.axi_d0_wr_vaild_cntr + record.axi_d0_rd_vaild_cntr == 0
    ):
        return True
    if hasattr(record, "m0_data_aw_cntr") and record.m0_data_aw_cntr == 0:
        return True
    return (
        hasattr(record, "thread_id_and_bank_conflict")
        and record.thread_id_and_bank_conflict == 2
    )


def omit_system_commands(engine_pairs, system_code, remain=False):
    for records in engine_pairs:
        _omit_system_commands(records, system_code, remain)


def _omit_system_commands(records, system_code, remain):
    while True:
        removed = False
        if (
            len(records) >= 3
            and records[-1].inst_id - records[-2].inst_id == 1
            and records[-2].inst_id <= records[-3].inst_id
            and (
                (
                    hasattr(records[-2], "cmd")
                    and get_command_type(records[-2].cmd)[0] == system_code
                )
                or hasattr(records[-2], "sys")
            )
        ):
            del records[-2:]
            removed = True
        elif (
            len(records) >= 3
            and records[1].inst_id - records[0].inst_id == 1
            and records[2].inst_id <= records[1].inst_id
            and (
                (
                    hasattr(records[0], "cmd")
                    and get_command_type(records[0].cmd)[0] == system_code
                )
                or hasattr(records[0], "sys")
            )
        ):
            del records[0:2]
            removed = True
        if not removed:
            break

    if not remain and len(records) <= 2:
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            if hasattr(record, "sys") or (
                hasattr(record, "cmd")
                and get_command_type(record.cmd)[0] == system_code
            ):
                records.pop(index)


def _minimum_edit_pairs(monitor_sections, command_sections):
    monitor_count = len(monitor_sections)
    command_count = len(command_sections)
    costs = [
        [float("inf")] * (command_count + 1)
        for _ in range(monitor_count + 1)
    ]
    path = [[None] * (command_count + 1) for _ in range(monitor_count + 1)]
    costs[0][0] = 0
    for index in range(1, monitor_count + 1):
        costs[index][0] = index
        path[index][0] = (index - 1, 0)
    for index in range(1, command_count + 1):
        costs[0][index] = index
        path[0][index] = (0, index - 1)

    for monitor_index in range(1, monitor_count + 1):
        for command_index in range(1, command_count + 1):
            if (
                monitor_sections[monitor_index - 1][0]
                == command_sections[command_index - 1][0]
            ):
                costs[monitor_index][command_index] = costs[
                    monitor_index - 1
                ][command_index - 1]
                path[monitor_index][command_index] = (
                    monitor_index - 1,
                    command_index - 1,
                )
            else:
                options = (
                    (
                        costs[monitor_index - 1][command_index] + 1,
                        (monitor_index - 1, command_index),
                    ),
                    (
                        costs[monitor_index][command_index - 1] + 1,
                        (monitor_index, command_index - 1),
                    ),
                    (
                        costs[monitor_index - 1][command_index - 1] + 2,
                        (monitor_index - 1, command_index - 1),
                    ),
                )
                costs[monitor_index][command_index], path[monitor_index][
                    command_index
                ] = min(options, key=lambda option: option[0])

    monitor_index = monitor_count
    command_index = command_count
    result = []
    while monitor_index > 0 or command_index > 0:
        previous_monitor, previous_command = path[monitor_index][command_index]
        if (
            previous_monitor == monitor_index - 1
            and previous_command == command_index - 1
        ):
            result.append(
                (
                    monitor_sections[monitor_index - 1][1],
                    command_sections[command_index - 1][1],
                )
            )
        elif previous_monitor == monitor_index - 1:
            result.append((monitor_sections[monitor_index - 1][1], None))
        monitor_index, command_index = previous_monitor, previous_command
    result.reverse()
    return result


def _sections(records, get_id, mark_system=False):
    sections = []
    ids = []
    indices = []
    system_count = 0
    for index, record in enumerate(records):
        if record is None:
            continue
        if hasattr(record, "offset"):
            if indices:
                sections.append([ids[-1] - ids[0] + 1, indices])
            sections.append([record.cmd_num, [index]])
            indices, ids = [], []
        elif hasattr(record, "cmd"):
            continue
        else:
            if mark_system and is_system_monitor(record):
                record.sys = "send" if system_count % 2 == 0 else "wait"
                system_count += 1
            record_id = get_id(record)
            if ids and record_id <= ids[-1]:
                sections.append([ids[-1] - ids[0] + 1, indices])
                indices, ids = [], []
            ids.append(record_id)
            indices.append(index)
    if indices:
        sections.append([ids[-1] - ids[0] + 1, indices])
    return sections


def match_sections(monitor, commands, get_id):
    return _minimum_edit_pairs(
        _sections(monitor, get_id, mark_system=True),
        _sections(commands, get_id),
    )


def pair_sections(
    monitor,
    commands,
    get_id,
    *,
    descriptor_map=None,
    command_parser=None,
    drop_unmatched=False,
):
    for monitor_indices, command_indices in match_sections(
        monitor, commands, get_id
    ):
        if monitor_indices is None:
            continue
        if command_indices is None:
            if drop_unmatched:
                for index in monitor_indices:
                    monitor[index] = None
            continue

        monitor_start_id = monitor[monitor_indices[0]].inst_id
        decoded_commands = commands
        active_indices = command_indices
        for position, monitor_index in enumerate(monitor_indices):
            if position == 0:
                monitor_start_id = monitor[monitor_index].inst_id
                descriptor = commands[command_indices[position]]
                if hasattr(descriptor, "offset"):
                    if descriptor_map is None or command_parser is None:
                        continue
                    lazy_commands = descriptor_map.get(
                        descriptor.offset, {}
                    ).get(descriptor.cmd_num)
                    if lazy_commands is None:
                        for index in monitor_indices:
                            copy_command_tags(monitor[index], descriptor)
                        continue
                    decoded_commands = lazy_commands.parse(command_parser)
                    active_indices = range(len(decoded_commands))
            relative_index = monitor[monitor_index].inst_id - monitor_start_id
            if 0 <= relative_index < len(active_indices):
                command = decoded_commands[active_indices[relative_index]]
                monitor[monitor_index].cmd = command
                if hasattr(descriptor, "offset"):
                    copy_command_tags(monitor[monitor_index], descriptor, command)
                else:
                    copy_command_tags(monitor[monitor_index], command)
            else:
                copy_command_tags(monitor[monitor_index], descriptor)
    if drop_unmatched:
        monitor[:] = [record for record in monitor if record is not None]
