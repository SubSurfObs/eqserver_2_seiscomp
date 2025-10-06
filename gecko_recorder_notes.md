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

## Copying files

The following script copies over file on order to take either the underscored file (firts preference(, or the spaced file. It's been tested on a directre with a mix of file types

```                                                                                                    
#!/bin/bash

in_dir="$1"
out_dir="$2"

mkdir -p "$out_dir"

# Keep track of which minutes already have an underscored file
declare -A underscored_exists

for file in "$in_dir"/*.ms*; do
    [ -e "$file" ] || continue
    base=$(basename "$file")

    # Extract HHMM from underscored (e.g., 20231029_0001_ABM1Y.ms) or spaced (2023-10-29 0001 00 ABM1Y.ms)
    if [[ "$base" =~ ([0-2][0-9][0-5][0-9]) ]]; then
        hhmm="${BASH_REMATCH[1]}"
    else
        echo "Cannot extract HHMM from $base"
        continue
    fi

    if [[ "$base" == *_*_* ]]; then
        # Underscored file: copy and mark HHMM
        cp "$file" "$out_dir/"
        underscored_exists["$hhmm"]=1
        echo "Copied underscored: $file"
    else
        # Spaced file: only copy if no underscored for this HHMM
        if [[ -z "${underscored_exists[$hhmm]}" ]]; then
            cp "$file" "$out_dir/"
            echo "Copied spaced: $file"
        fi
    fi
done
```
## Example

`mseed_day_test` contains a range of underscored and scpaced files files
`ms_minute_clean` should contain a unique set up files 

```
./scripts/copy_minute_files_gecko.sh mseed_day_test/ temp_day_dir
#unzip them
find temp_day_dir -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > temp_day_dir/full.ms
scmssort -u -E temp_day_dir/full.ms > temp_day_dir/sorted.mseed
scart -I temp_day_dir/sorted.mseed       --with-filecheck       --rename "VW.-.-.-"       sds_test_archive/

```

```
project/
├── scripts/
│   ├── process_day_gecko.sh
│   ├── process_month_gecko.sh
│   ├── process_year_gecko.sh
│   ├── process_archive_gecko.sh
│   └── copy_minute_files_gecko.sh
├── temp_processing/
└── sds_test_archive/


```

Running this on one day results in 2 files ( a big one and a small one). Big file seems good. 

```
./scripts/process_day_gecko.sh /data/repository/archive/ABM1Y/continuous/2023/10/24 sds_test_archive temp_processing
ls -lrt sds_test_archive/2023/VW/ABM1Y/CHZ.D/
total 32180
-rw-rw-r-- 1 seiscomp seiscomp    19968 Oct  6 16:04 VW.ABM1Y.00.CHZ.D.2023.298
-rw-rw-r-- 1 seiscomp seiscomp 32929280 Oct  6 16:04 VW.ABM1Y.00.CHZ.D.2023.297
```

I think this probably occurs because the EqServer files spill over. This is not necessarily a problem. 

