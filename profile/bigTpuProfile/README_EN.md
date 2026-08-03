# bigTpuProfile

bigTpuProfile is a performance visualization tool for accelerator cards, supporting AKS and AKSV.

## Table of Contents

- [Quick Start](#quick-start)
- [Profile Data Export](#profile-data-export)
  - [Operators](#operators)
    - [tpu-train/tgi](#tpu-traintgi)
    - [tpudnn](#tpudnn)
  - [bmodel](#bmodel)
- [Visualization](#visualization)
- [Data Analysis](#data-analysis)
  - [Directory Structure](#directory-structure)
  - [doc](#doc)
  - [web](#web)
  - [perfetto](#perfetto)
  - [summary](#summary)
- [License](#license)

## Quick Start

Using bigTpuProfile mainly consists of two steps:

1. Export profile data
2. Visualize it with bigTpuProfile

## Profile Data Export

### Operators

1. bigTpuProfile has three modes:

    1) Mode 0, PMU only: does not care about the concrete command types inside operators, only the time dimension. This mode has the smallest performance impact.

    2) Mode 1, condensed commands: records the command types inside operators. This mode has a relatively small performance impact, but DMA bandwidth statistics are inaccurate (extra overhead: ~4%).

    3) Mode 2, detailed commands: records detailed information for each command inside operators. This is usually used for debugging and DMA bandwidth statistics (extra overhead: 7-10%).

2. `max_record_num` is the maximum number of profile records. Make sure the configured value is larger than the actual number of records.

3. Profile output file naming rule:

   `cdm_profile_data_dev{DeviceID}-{CallNum}`

   Profile can run independently on multiple devices, with `DeviceID` marked in the file name. It can also be called multiple times on the same device, with `CallNum` marked in the file name.

#### tpu-train/tgi

```python
torch.ops.my_ops.enable_profile(max_record_num, mode)  # Set the recording start point (record cmd information, mode: 0 PMU only, 1 condensed commands, 2 detailed commands)

torch.ops.my_ops.disable_profile()  # Set the recording end point
```

```python
# tpu-train example
# part 0
torch.ops.my_ops.enable_profile(max_record_num, 0)  # enable profile without cmd info (pure pmu
_ = a tpu * b tpu
torch.ops.my_ops.disable_profile()  # disable profile and dump data (cdm_profile_data_dev0-0)
# part 1
torch.ops.my_ops.enable_profile(max_record_num, 1)  # enable profile with condensed cmd info
_ = a tpu + b tpu
torch.ops.my_ops.disable_profile()  #(cdm_profile_data_dev0-1)
# part 2
torch.ops.my_ops.enable_profile(max_record_num, 2)  # enable profile with detailed cmd info
_ = a tpu + b tpu
torch.ops.my_ops.disable_profile()  #(cdm_profile_data_dev0-2)
```

```python
# tgi (text-generation-inference) example
# test_whole_parallel.py

def test_whole_model(batches=1, model_id="llama", model_path='/data', quantize=None, mode="chat"):
    .....
    for it in range(2):
        .....
        for i in range(DECODE_TOKEN_LEN):
            os.environ["TOKEN_IDX"] = str(i)
            generate_start = time.time_ns()
            generations, next_batch, (forward_ns, decode_ns) = model.generate_token(
                next_batch
            )
            generate_end = time.time_ns()
            time_list.append(generate_end - generate_start)
            for generation in generations:
                if i == 0:
                    generated_text[generation.request_id] = generation.tokens.texts[0]
                else:
                    generated_text[generation.request_id] += generation.tokens.texts[0]
            if decode_only and enable_profile and it > 0 and i == 0:                 # condition
                torch.ops.my_ops.enable_profile(max_record_num, book_keeping)        # enable profile
            logger.info(f"Token {i} {[g.tokens.texts[0] for g in generations]}")
            if next_batch is None:
                break

        if enable_profile and it > 0:                                                # condition
            torch_tpu.tpu.optimer_utils.OpTimer_dump()
            torch.ops.my_ops.disable_profile()                                       # disable profile
  .....

```

#### tpudnn

```c++
// Assuming the handle type is: tpudnnHandle_t
auto pimpl = static_cast<TPUDNNImpl *>(handle);

pimpl->enableProfile(max_record_num, mode);  // Set the recording start point (record cmd information, mode: 0 PMU only, 1 condensed commands, 2 detailed commands)
pimpl->disableProfile();  // Set the recording end point
```

```c++
// tpudnn example
....
const int group_num =1;
const int group_size = pimpl->getCoreNum();
pimpl->enableProfile();    // enable profile
status = pimpl->launchKernel("gelu_forward_multi_core", &api, sizeof(api), group_num, group_size);
pimpl->disableProfile();   // disable profile
pimpl->enableProfile(80);  // enable profile
status = pimpl->launchKernel("gelu_forward_multi_core", &api, sizeof(api), group_num, group_size);
pimpl->disableProfile();   // disable profile
return status;
```

### bmodel

Unlike operator profiling, bmodel profiling is mainly controlled through environment variables. You only need to check whether the maximum record count is appropriate, and do not need to set the mode:

- `ENABLE_ALL_PROFILE=1`: Enable profile

- `TPUKERNEL_FIRMWARE_PATH=/home/xxx/libfirmware_core.so`: Set `firmware.so`; required if the bmodel version is too old

- `PROFILE_MODE`: Optional. Valid values are 1 and 2. Shows extra profile information, such as input/output transfer information.

- `PROFILE_RECORD_SIZE`: Optional, default value 131072. Sets the maximum number of PMU records.

```bash
# Use the default libfirmware_core.so and record count
ENABLE_ALL_PROFILE=1 tpu-model-rt --bmodel ./xxx.bmodel

# Specify firmware.so, record count, and view input/output transfer performance
TPUKERNEL_FIRMWARE_PATH=/home/xxx/libfirmware_core.so ENABLE_ALL_PROFILE=1  PROFILE_MODE=2 PROFILE_RECORD_SIZE=40960 tpu-model-rt  --bmodel ./xxx.bmodel
```

## Visualization

Parse and visualize profile data with bigTpuProfile:

```bash
# Use bigTpuProfile -h to view available arguments
bigTpuProfile <input_dir> <output_dir> [options]
```

### Command Line Arguments

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `input_dir` | str | Required | Profile data directory generated by bmruntime |
| `output_dir` | str | `profile_out` | Output directory for parsed results |
| `--disable_summary` | flag | False | Disable summary output |
| `--enable_doc` | flag | False | Generate doc visualization |
| `--trace_file` | str | None | tpudnn trace file to merge (used for the tpu-train profile interface) |
| `--tiu_freq` | int | 1000 | Set TIU frequency (MHz) |

### Usage Examples

```bash
# Basic usage: generate Perfetto trace and summary
bigTpuProfile cdm_profile_data_dev0-0/ result_out

# Generate doc visualization
bigTpuProfile cdm_profile_data_dev0-0/ result_out --enable_doc

# Merge an external trace file and customize TIU frequency
bigTpuProfile cdm_profile_data_dev0-0/ result_out --trace_file merged_trace.json --tiu_freq 800

```

## Data Analysis

### Directory Structure

```
/result_out/
├── PerfDoc   Doc visualization results
├── perfetto.pftrace   Perfetto visualization file
├── summary.txt   Performance statistics summary
├── tiuRegInfo_x
├── tdmaRegInfo_x
└── cdmaRegInfo_x
```

### doc

`PerfAI_output.xlsx`, generated with `--enable_doc`, includes an overview and valid data recorded by each engine (TIU, GDMA, SDMA, CDMA) on each core.

The naming rule is: `engineType_coreId`

![](./img/doc.png)


### Perfetto

The generated `perfetto.pftrace` file can be visualized with [Perfetto UI](https://ui.perfetto.dev) or an installed Perfetto Trace. For the complete usage guide, see the [official documentation](https://perfetto.dev/docs/visualization/perfetto-ui):

1. Open https://ui.perfetto.dev
2. Drag the `perfetto.pftrace` file into the page to view the instruction execution timeline for each engine (TIU/GDMA/SDMA/CDMA)
3. Use WASD to zoom the view. Range selection and click-to-view-details are supported.
4. The data has been SQL-ified. The following SQL statements can help with analysis.

```SQL

// Filter all "mm.normal" items and show them by duration in descending order
select * from slice s
where s.name="mm.normal"
order by dur desc

// List operator durations under a specified core
SELECT s.*
FROM slice s
JOIN track t ON s.track_id = t.id
JOIN track parent ON t.parent_id = parent.id
WHERE parent.name = 'Core 0' AND t.name = 'Kernel Function'
ORDER BY s.dur DESC;

// Filter all transfer items whose bandwidth is between 5 and 20
SELECT a.real_value AS ddr_bandwidth, s.*  FROM slice s
JOIN args a ON s.arg_set_id = a.arg_set_id
WHERE a.key = 'debug.DDR Bandwidth(GB/s)'
  AND ddr_bandwidth BETWEEN 5 AND 20
order by ddr_bandwidth desc;

// Filter all items whose TIU utilization is below 40%
// uArchRate values include '%' and are stored as text, so display_value must be read and cast to DOUBLE
SELECT a.display_value  AS uArchRate, s.* FROM slice s
JOIN args a ON s.arg_set_id = a.arg_set_id
WHERE a.key = 'debug.uArch Rate'
  AND CAST(a.display_value  AS DOUBLE) < 40
order by CAST(uArchRate AS DOUBLE) desc;
```

If an external tpudnn trace is merged through `--trace_file`, the aligned host-side and TPU-side timelines can also be viewed together in Perfetto.
This parameter is currently integrated in tpu-train and can be merged automatically by calling `export_merge_trace`.

![](./img/perfetto.png)

### Summary

`summary.txt` is automatically generated and contains per-core performance statistics:

- Effective time, idle time, and utilization for each engine (TIU/GDMA/SDMA/CDMA)
- DDR bandwidth statistics (GDMA/SDMA/CDMA read/write bandwidth and total bandwidth)
- Compute and transfer time ratio

A formatted summary table is printed to the console, and `summary.txt` is also generated.

![](./img/summary.png)

## License

bigTpuProfile is licensed under the 2-Clause BSD License, excluding third-party components.
