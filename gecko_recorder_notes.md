## Miniseed data from Geckos

## Zipped files

Here is an example where we have a telemetered and non-telemetered file:

```
'/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip'
/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip
  inflating: test_unzip_formats/2023-10-29 0001 00 ABM1Y.ms  
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
  inflating: test_unzip_formats/20231029_0001_ABM1Y.ms  
  inflating: test_unzip_formats/ABM1Y.02000236.ss  
  inflating: test_unzip_formats/ABM1Y.02000236.station.xml
```

So, the underscored zip file (USB/Local file) contains multiple files


### Example copy files 
`mseed_day_test` contains a range of underscored and scpaced files files
`ms_minute_clean` should contain a unique set up files 

```
./scripts/copy_minute_files.sh mseed_day_test/ ms_minute_clean

### Unzip and cat miniseed files

#unzip them
find temp_day_dir -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > temp_day_dir/full.ms
scmssort -u -E temp_day_dir/full.ms > temp_day_dir/sorted.mseed
scart -I temp_day_dir/sorted.mseed       --with-filecheck       --rename "VW.-.-.-"       sds_test_archive/

```

```



```

## Process days/months/year


Day-level wrapper: `process_day_gecko.sh`
Purpose: Process one day of data.
What it does:
- Copies all files from the day directory to a temp folder.
- Calls copy_minute_files_gecko.sh to handle duplicates.
- Unzips any .zip files.
- Concatenates .ms files safely.
- Sorts and merges MiniSEED headers.
- Imports into the SDS archive, renaming network to VW.
- Cleans up temp files.

Test case:
`./scripts/process_day_gecko.sh /data/repository/archive/ABM1Y/continuous/2023/10/23 sds_test_archive temp_processing`

Running this on one day results in 2 files ( a big one and a small one). Big file seems good. 

```
./scripts/process_day_gecko.sh /data/repository/archive/ABM1Y/continuous/2023/10/24 sds_test_archive temp_processing
ls -lrt sds_test_archive/2023/VW/ABM1Y/CHZ.D/
total 32180
-rw-rw-r-- 1 seiscomp seiscomp    19968 Oct  6 16:04 VW.ABM1Y.00.CHZ.D.2023.298
-rw-rw-r-- 1 seiscomp seiscomp 32929280 Oct  6 16:04 VW.ABM1Y.00.CHZ.D.2023.297
```

I think this probably occurs because the EqServer files spill over. This is not necessarily a problem. 

Month-level wrapper: `process_month_gecko.sh`
Purpose: Process all days in a month.
What it does:
- Loops over numeric day subdirectories inside the month folder.
- Skips empty days.
- Calls process_day_gecko.sh for each day.
- 
Test case:
`./scripts/process_month_gecko.sh /data/repository/archive/ABM1Y/continuous/2023/10 sds_test_archive temp_processing`

Year-level wrapper: `process_year_gecko.sh`

Purpose: Process all months in a year.
What it does:
- Loops over numeric month subdirectories (01–12).
- Skips empty months.
- Calls process_month_gecko.sh for each month.
Test case:
`./scripts/process_year_gecko.sh /data/repository/archive/ABM1Y/continuous/2023 sds_test_archive temp_processing`

Key idea: Each wrapper delegates work to the next lower level, allowing you to process a day, month, or year with the same underlying day-processing logic, while handling temp directories and avoiding spillover between days.

project/
├── scripts/
│   ├── process_day_gecko.sh
│   ├── process_month_gecko.sh
│   ├── process_year_gecko.sh
│   ├── process_archive_gecko.sh
│   └── copy_minute_files_gecko.sh
├── temp_processing/
└── sds_test_archive/

