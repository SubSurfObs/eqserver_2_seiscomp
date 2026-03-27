## Miniseed data from Geckos on EQserver

## Gecko data on EqServer 

It's not as homogenous as I hoped. 

More recent data seems to use the same space/underscore convention for telemetered / local files. 

Some Examples:

* 2023-10-29 0001 00 ABM1Y.ms.zip
* 2023-11-01 1020 00 BRTH.ms.zip'
* 20231029_0001_ABM1Y.ms.zip
* '2020-11-08 0001 DDSW.ms.zip'

Some important differences to EchoData

* Gecko underscored files have 2 underscores whereas EchoPro's have 3. They don't contain a "second" timestamp
* Gecko spaced files sometimes have a seconds field, but not always, and example is 2020-11-08 0001 DDSW.ms.zip'

Caveats 

DDNE data from early looks very different - NOTE not zipped. 

* '2017-10-05 0000 58 DDNE_CHE.mseed' (note mseed and CHE, but not mseed.zip)

By 2018, it has a more regular file structre:

* '2018-10-05 0000 DDNE.ms.zip,  20181005_0000_DDNE.ms.zip

This day for FORG has 2 types of spaced file (2905 files in total): 

* '2021-11-03 0001 00 FORG.ms.zip'
* '2021-11-03 0001 FORG.ms.zip'

Is this an earlier version, where underscores were not used for local files, but instead the absence of a seconds in the timestamp is indicative?

LRSH data from the same period has the space/underscre convention

* 20211103_0000_LRSH.ms.zip
* '2021-11-03 0001 LRSH.ms.zip'

Files to watch out for, exclude

You sometime see triggered files with a dmx suffix (which is similar to occasionally seeing mseed files for Echo staations. 

* '2023-11-01 1009 59 BRTH.trig.dmx'
* '2018-10-25 2000 00 BRTH.ss'
* '2018-10-25 1542 03 BRTH_CHZ.mseed.zip' (possible a triggered vertical file, but note that early 2017 DDNE data has this format. except not zipped )
* In this case, '2023-10-24 2004 11 DDSW_CHZ.mseed.zip' all files with this format have a differetn second stamp - consistent with triggered data. 

Files to watch out for, exclude:

* trig
* dmx
* mseed.zip
* ss

## Zipped files

Are Gecko files always zipped on Eqserver?

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

So, the underscored zip file (USB/Local file) contains multiple files, the spaced file contains a sinle file


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

