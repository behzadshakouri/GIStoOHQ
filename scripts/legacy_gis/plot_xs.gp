# =============================================================================
# plot_xs.gp  --  render one PNG per HEC-RAS cross section
#
# Input is the gnuplot BLOCK file from step 2b (ras_xs_profiles.py): each cross
# section is one `index` (blocks separated by two blank lines), two columns
# "offset_m elev_m", with a "# xs_id=.. station_m=.. river_sta_ft=.." header.
#
# USAGE:
#   gnuplot -e "dat='/.../outputs_RAS/xs_profiles.gp.dat'; \
#                outdir='/.../outputs_RAS/xs_plots'" plot_xs.gp
#
# Defaults (if -e is omitted): dat in CWD, outdir='xs_plots'.
# Creates outdir if missing and writes xs_000.png, xs_001.png, ...
# =============================================================================

# --- inputs / defaults ------------------------------------------------------
if (!exists("dat"))    dat    = "xs_profiles.gp.dat"
if (!exists("outdir")) outdir = "xs_plots"

# make the output folder (portable-ish: works on Linux/macOS shells)
system(sprintf("mkdir -p '%s'", outdir))

# --- count cross-section blocks --------------------------------------------
# stats sets STATS_blocks = number of data blocks (indices) in the file.
stats dat using 1 nooutput
nblocks = STATS_blocks
print sprintf("cross-section blocks found: %d", nblocks)

# --- common style -----------------------------------------------------------
set encoding utf8
set terminal pngcairo size 900,500 enhanced font "Helvetica,11"
set grid lc rgb "#cccccc"
set xlabel "Offset from centerline (m), left negative / right positive"
set ylabel "Ground elevation (m)"
set key off
set style line 1 lc rgb "#1F3864" lw 2 pt 7 ps 0.3
set style line 2 lc rgb "#C0392B" lw 1 dt 2    # centerline marker

# --- one PNG per block ------------------------------------------------------
# gnuplot indices are 0-based; xs_id was written 0-based too, so index i == xs_id i.
do for [i=0:nblocks-1] {
    set output sprintf("%s/xs_%03d.png", outdir, i)

    # pull this block's header line and parse the numeric values out of it, so
    # the title is clean (no raw "xs_id=" text, no underscore-as-subscript).
    hdr = system(sprintf("grep '^# xs_id=' '%s' | sed -n '%dp'", dat, i+1))
    sta = system(sprintf("echo '%s' | sed -n 's/.*station_m=\\([0-9.]*\\).*/\\1/p'", hdr))
    riv = system(sprintf("echo '%s' | sed -n 's/.*river_sta_ft=\\([0-9.]*\\).*/\\1/p'", hdr))
    set title sprintf("Cross section %d    station %s m    river sta %s ft", \
                      i, sta, riv) noenhanced textcolor rgb "#1F3864"

    # vertical line at offset 0 (channel centerline), then the profile.
    set arrow 1 from 0,graph 0 to 0,graph 1 nohead ls 2
    plot dat index i using 1:2 with linespoints ls 1
    unset arrow 1
}

unset output
print sprintf("done: wrote %d PNG(s) to %s", nblocks, outdir)
