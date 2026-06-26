# =============================================================================
# plot_hydrographs.gp  --  plot one group of HMS FLOW hydrographs
#
# Input is a group block-file from hms_dss_to_gnuplot.py: one cross-... one
# RECORD per `index` (blocks separated by two blank lines), columns
# "hours  flow_cfs", with a "# location=..  pathname=.." header per block.
# All records in the group are overlaid on a single figure, legend by location.
#
# USAGE:
#   gnuplot -e "dat='/.../dss_plots/all_junctions.gp.dat'; \
#                out='/.../dss_plots/all_junctions.png'" plot_hydrographs.gp
#
# Optional: title='My title'   (else taken from the file's first header)
# =============================================================================

if (!exists("dat")) { print "ERROR: pass dat='...gp.dat'"; exit }
if (!exists("out")) out = "hydrographs.png"

# number of records (blocks) in this group
stats dat using 1 nooutput
nrec = STATS_blocks
print sprintf("records in group: %d", nrec)

set terminal pngcairo size 1000,560 enhanced font "Helvetica,11"
set encoding utf8
set output out
set key noenhanced       # keep underscores literal in location labels

set grid lc rgb "#cccccc"
set xlabel "Time (hours from start)"
set ylabel "Flow (cfs)"
set key outside right top box lc rgb "#aaaaaa" font "Helvetica,9"
if (exists("title")) { set title title textcolor rgb "#1F3864" } \
else { set title "HMS flow hydrographs" textcolor rgb "#1F3864" }

# a navy->amber-ish qualitative palette cycled across records
array C[8] = [ "#1F3864", "#C0392B", "#3E6B96", "#E67E22", \
               "#5B8C5A", "#8E44AD", "#117A8B", "#B7950B" ]

# extract each block's location label for the legend
# (grep the i-th "# location=" line, strip to the value)
plot for [i=0:nrec-1] dat index i using 1:2 \
     with lines lw 2 lc rgb C[(i % 8) + 1] \
     title system(sprintf("grep '^# label=' '%s' | sed -n '%dp' | sed -n 's/.*label=\\(.*\\)  location=.*/\\1/p'", dat, i+1))

unset output
print sprintf("wrote %s", out)
