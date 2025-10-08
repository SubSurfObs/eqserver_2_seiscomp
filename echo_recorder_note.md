# PC-SUDS data from Echos and EchoPros

## overview

* Records minute long PC-SUDS files, with extension `.dmx`
* Recorder has an option to compress, which gives`.dmx.gz`
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx`
* Because EchoPros are 6 channels, there are potential complications relative to Geckos. This is mainly related to triggered files that don;t have a "trig" in the file name


## Unzipping

e.g., 

```
#-d decompress, k keep oroginal file
gzip -dk 2024-01-01\ 0000\ 52\ FRTM.dmx.gz
```

This results in a single PC-SUDS file, eg *.FRTM.dmx

## SUDS Data in EqServer

In the EqServer Continuous Archive you may find:

* both telemetered and locally saved files
* Telemetered files have spaces: `2024 2024-01-01 2359 02 ABM5Y.dmx`
* Locally saved files generalyy have underscores: `2024 2024-01-01_2359_02_ABM5Y.dmx

Caveats / anomalies
  
* sometimes you also get triggered files in the continuous , these files look like `...trig.dmx.gz`
* sometimes you get a few errant mseed files (??) that have single underscore, like '2023-10-24 0836 49 MARD_DHZ.mseed.zip' (this is one of about 50 such files in a day that otherwise has only telemetered data)
* sometimes you get accelerometer files that are triggered and do not have trig in the name e.g., `2023-11-24_0317_55_ABM5Y.dmx`. this is harder to deal with.
* sometimes, it seems like an underscred files genuainely replaces a spaced file, as in '2023-12-12 2009 27 ABM2Y.dmx',  2023-12-12_2010_27_ABM2Y.dmx, '2023-12-12 2011 27 ABM2Y.dmx'. This is in a directory that has 95 percrent spaced files. 


## filtering correct files:

sometimes you get accelerometer files that are triggered and do not have trig in the name e.g., `2023-11-24_0317_55_ABM5Y.dmx`. this is harder to deal with. Howverm because these are triggered , they do not have a common time stamp (as in seconds). 

```
seiscomp@rd-l-y9d9pt:~/sds_conversion_tests$ ls /data/repository/archive/ABM5Y/continuous/2023/11/24  | awk -F'_' 'NF==4' | wc -l
1480
```

One way of dealing with this is to check for files that have a uniform timestamp. My hope is that this cover something like 80-90% of cases, i.e. recorder functioning and 1440 files present. So this is the first check to perform - look for a full (or near full) complement of files.

This one-liner loops over all day directories in the specified month and runs report_echo_day.sh on each, producing a report of the most frequent timestamps for underscored and spaced files per day.

```
for d in /data/repository/archive/ABM2Y/continuous/2023/12/*; do ./report_echo_day.sh "$d"; done
```

If this returns less that 1440, there are a few options. We could copy over the files that have a unique pattern and are abouve a threshold number of files. This should get the continuous files even when the recorder was switching on and off. 

So the logic might be:

* Check total underscored and spaced files, excluding known file patterns like "trig", "mseed". etc/
* check if underscored > spaced files
* if there are ~1440 underscored files copy over, break
* if there are ~1440 spaced files, and underscored files  below threshold, copy over spaced files, break
* if there are more than 1440 underscored files this might imply additional channels.
* get unique time stamps and frequency on underscored files
* check if one of these combnations has ~1440, if so copy these,break
* if not, count those above a threshold to get a total. 
* if this is less than copy over, 1440, break
* if there are less than 1440 files, try to patch in spaced files

The limitation in this logic will be if triggered accelerometer files with the same pattern overwrite the seismometer files, and coincidently have the same file time stamp.  In that case, we may still see case where 6 channels appear. 

Of course, you could screen for these by using a temporary directory for the output of `scart`. You use this to check for more than 3 streams. If found, you could remove. 

```
$ scripts/count_underscored.sh /data/repository/archive/ABM5Y/continuous/2023/11/24
Directory: /data/repository/archive/ABM5Y/continuous/2023/11/24
Most common timestamp: 02
Number of files with this timestamp and 3 underscores: 1440
```
```
$ ./scripts/iter_count_underscored.sh /data/repository/archive/NARR/continuous/2018/10/01
Directory: /data/repository/archive/NARR/continuous/2018/10/01
Total underscored files: 1440
Most common timestamp: 49
Number of files matching this timestamp: 1129
Most common timestamp: 24
Number of files matching this timestamp: 311
```

```
$ ./scripts/iter_count_spaced.sh  /data/repository/archive/ABM2Y/continuous/2023/12/01
Directory: /data/repository/archive/ABM2Y/continuous/2023/12/01
Total files considered: 1439
Most common timestamp: 34
Number of files matching this timestamp: 1439
```



Tests:

```
=>mainly underscored files
/data/repository/archive/ABM2Y/continuous/2023/11/01
=>mainly spaced files
/data/repository/archive/ABM2Y/continuous/2023/12/01
```


## Use of EqConvert on SUDS data

Here is a test with 3 files. The spaced file "02" is telemetered 1 componenent, the underscored file "02" is local 3 componet, the other files is a triggered accelerometer file. 

```
$ ls -lrt
-rwxr-xr-x 1 seiscomp seiscomp 31207 Oct  8 21:45 '2023-11-24 2057 02 ABM5Y.dmx'
-rwxr-xr-x 1 seiscomp seiscomp 93546 Oct  8 21:45  2023-11-24_2057_48_ABM5Y.dmx
-rwxr-xr-x 1 seiscomp seiscomp 91915 Oct  8 21:45  2023-11-24_2057_02_ABM5Y.dmx
```
Runningg this in output directory mode, results in 2 output files:

```
seiscomp@rd-l-y9d9pt:~/sds_conversion_tests/scmssort_tests/echo_overlaps$ java -jar /home/sysop/mnt/software/eqconvert.7/eqconvert.jar ./ -f miniseed -d ms_convert
2023-11-24_2057_48_ABM5Y.dmx->/home/seiscomp/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert/2023-11-24 2057 48 ABM5Y.ms
2023-11-24_2057_02_ABM5Y.dmx->/home/seiscomp/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert/2023-11-24 2057 02 ABM5Y.ms
2023-11-24 2057 02 ABM5Y.ms->/home/seiscomp/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert/2023-11-24 2057 02 ABM5Y.ms
2023-11-24 2057 48 ABM5Y.ms->/home/seiscomp/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert/2023-11-24 2057 48 ABM5Y.ms
2023-11-24 2057 02 ABM5Y.dmx->/home/seiscomp/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert/2023-11-24 2057 02 ABM5Y.ms
```
However, when I concatentaed these files, then used scart to push them into SDS, I found:

```
$ ls test_sds/2023/AB/ABM5Y/
DLZ.D  DNE.D  DNN.D  DNZ.D
```

It appears that the 3 component data was lost, as a result of including the single component file. Not good. I tested this by running the same workflow withut the single channel data, and I got:

```
seiscomp@rd-l-y9d9pt:~/sds_conversion_tests/scmssort_tests/echo_overlaps/ms_convert$ ls test_sds/2023/AB/ABM5Y/
DLE.D  DLN.D  DLZ.D  DNE.D  DNN.D  DNZ.D
```

However, when I wrote all three SUDS files to a single miniseed file, all channels were retained!
```
java -jar /home/sysop/mnt/software/eqconvert.7/eqconvert.jar ./ -f miniseed -w ms_convert/single.ms
```

The next problem though, is that whenever I use eqconvert to merge minseed files, I then find that the network mapping in scart is difficult. I have got this working;

```
cat *.ms | scmssort -uE > outputs/sorted.mseed
scart -I outputs/sorted.mseed --with-filecheck test_sds2/ -c "DL?" --rename "VW.-.-.-"
```

## Copying data for conversion

The script loops over every hour and minute of the day, constructing an HHMM string for each, and for each HHMM it copies all matching underscored .dmx or .dmx.gz files from the input directory to the output directory, skipping any .trig files; if no underscored files exist for that HHMM, it copies all matching spaced files instead, ensuring every available minute file is captured while preferring underscored files and handling multiple files per minute.

This has been through several iterations, as it broke on different cases. Best to keep it as a standalone script.

```
#!/bin/bash
# Usage: ./copy_minute_files.sh input_dir output_dir

in_dir="$1"
out_dir="$2"

if [ -z "$in_dir" ] || [ -z "$out_dir" ]; then
    echo "Usage: $0 input_dir output_dir"
    exit 1
fi

# Ensure absolute path for output
out_dir=$(realpath "$out_dir")
mkdir -p "$out_dir"

# Loop over hours and minutes
for hh in $(seq -w 0 23); do
    for mm in $(seq -w 0 59); do
        hhmm="${hh}${mm}"

        # Check if any underscored files exist for this HHMM
        if find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print -quit | grep -q .; then
            # Copy all underscored files safely
            find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied underscored: $file"
            done
        else
            # Copy all spaced files safely if no underscored files exist
            find "$in_dir" -maxdepth 1 -type f -name "* $hhmm *" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied spaced: $file"
            done
        fi

    done
done
```
