from collections import OrderedDict
import os

from imbie2.proc.sum_basins import sum_basins
from imbie2.conf import ImbieConfig
from imbie2.const.basins import IceSheet, BasinGroup
from imbie2.model.collections import WorkingMassRateCollection, MassChangeCollection, MassRateCollection
from imbie2.plot.plotter import Plotter
from imbie2.table.tables import MeanErrorsTable, TimeCoverageTable, BasinsTable


def process(input_data: MassRateCollection, config: ImbieConfig):

    groups = ["RA", "GMB", "IOM"]
    if config.include_la:
        groups.append("LA")
    for g in config.methods_skip:
        groups.remove(g)

    sheets = [IceSheet.apis, IceSheet.eais, IceSheet.wais, IceSheet.gris]
    regions = OrderedDict([
        (IceSheet.eais, [IceSheet.eais]),
        (IceSheet.apis, [IceSheet.apis]),
        (IceSheet.wais, [IceSheet.wais]),
        (IceSheet.ais, [IceSheet.apis, IceSheet.eais, IceSheet.wais]),
        (IceSheet.gris, [IceSheet.gris]),
        (IceSheet.all, [IceSheet.apis, IceSheet.eais, IceSheet.wais, IceSheet.gris])
    ])
    offset = config.align_date

    # normalise dM/dt data
    rate_data = input_data.chunk_series()

    # find users who have provided a full ice sheet of basin data, but no ice sheet series.
    sum_basins(rate_data, sheets)

    # keep copies of zwally/rignot data before merging them
    zwally_data = rate_data.filter(basin_group=BasinGroup.zwally)
    rignot_data = rate_data.filter(basin_group=BasinGroup.rignot)

    # merge zwally/rignot
    rate_data.merge()

    mass_data = rate_data.integrate(offset=offset)

    # create empty collections for storing outputs
    groups_sheets_rate = WorkingMassRateCollection()
    groups_sheets_mass = MassChangeCollection()

    groups_regions_rate = WorkingMassRateCollection()
    groups_regions_mass = MassChangeCollection()

    sheets_rate = WorkingMassRateCollection()
    sheets_mass = MassChangeCollection()

    regions_rate = WorkingMassRateCollection()
    regions_mass = MassChangeCollection()

    for outlier in config.users_mark:
        data = rate_data.filter(user=outlier)
        for series in data:
            for t, dmdt, e in zip(series.t, series.dmdt, series.errs):
                print(outlier, series.basin_id, t, dmdt, e)

    for group in groups:
        for sheet in sheets:
            print("computing", group, "average for", sheet.value, end="... ")

            new_series = rate_data.filter(
                user_group=group, basin_id=sheet
            ).average(mode=config.combine_method, nsigma=config.average_nsigma)
            if new_series is None:
                continue

            groups_sheets_rate.add_series(new_series)
            groups_sheets_mass.add_series(
                new_series.integrate(offset=offset)
            )
            print("done.")
        for region, sheets in regions.items():
            print("computing", group, "average for", region.value, end="... ")

            region_rate = groups_sheets_rate.filter(
                user_group=group, basin_id=sheets
            ).sum(error_method=config.sum_errors_method)
            if region_rate is None:
                continue

            region_rate.basin_id = region
            region_mass = region_rate.integrate(offset=offset)

            groups_regions_rate.add_series(region_rate)
            groups_regions_mass.add_series(region_mass)
            print("done.")

    output_path = os.path.expanduser(config.output_path)
    for sheet in sheets:
        print("computing inter-group average for", sheet.value, end="... ")

        sheet_rate_avg = groups_sheets_rate.filter(
            basin_id=sheet
        ).average(mode=config.combine_method, nsigma=config.average_nsigma,
                  export_data=os.path.join(output_path, sheet.value+"_data.csv"))
        if sheet_rate_avg is None:
            continue

        sheets_rate.add_series(sheet_rate_avg)
        sheets_mass.add_series(
            sheet_rate_avg.integrate(offset=offset)
        )
        print("done.")

    # compute region figures
    for region, sheets in regions.items():
        print("computing inter-group average for", region.value, end="... ")

        region_rate = sheets_rate.filter(
            basin_id=sheets
        ).sum(error_method=config.sum_errors_method)
        if region_rate is None:
            continue

        region_rate.basin_id = region

        regions_rate.add_series(region_rate)
        regions_mass.add_series(
            region_rate.integrate(offset=offset)
        )
        print("done.")

    # print tables
    output_path = os.path.expanduser(config.output_path)

    met = MeanErrorsTable(rate_data, style=config.table_format)
    filename = os.path.join(output_path, "mean_errors."+met.default_extension())

    print("writing table:", filename)
    met.write(filename)

    btz = BasinsTable(zwally_data, BasinGroup.zwally, style=config.table_format)
    filename = os.path.join(output_path, "zwally_basins."+btz.default_extension())

    print("writing table:", filename)
    btz.write(filename)

    btr = BasinsTable(rignot_data, BasinGroup.rignot, style=config.table_format)
    filename = os.path.join(output_path, "rignot_basins." + btr.default_extension())

    print("writing table:", filename)
    btr.write(filename)

    for group in groups:
        tct = TimeCoverageTable(rate_data.filter(user_group=group), style=config.table_format)
        filename = os.path.join(output_path, "time_coverage_" + group + "." + tct.default_extension())

        print("writing table:", filename)
        tct.write(filename)

    # draw plots
    plotter = Plotter(
        filetype=config.plot_format,
        path=output_path,
        limits=True
    )
    # rignot/zwally comparison
    for sheet in sheets:
        plotter.rignot_zwally_comparison(
            rignot_data+zwally_data, [sheet]
        )
    # error bars (IMBIE1 style plot)
    window = config.bar_plot_min_time, config.bar_plot_max_time
    plotter.sheets_error_bars(
        groups_regions_rate, regions_rate, groups, regions, window=window
    )
    plotter.sheets_error_bars(
        groups_regions_rate, regions_rate, groups, regions,
        window=window, ylabels=True, suffix="labeled"
    )

    align_dm = offset is None
    # intracomparisons
    for group in groups:
        plotter.group_rate_boxes(
            rate_data.filter(user_group=group), {s: s for s in sheets}, suffix=group
        )
        plotter.group_rate_intracomparison(
            groups_regions_rate.filter(user_group=group).smooth(config.plot_smooth_window),
            rate_data.filter(user_group=group).smooth(config.plot_smooth_window),
            regions, suffix=group, mark=config.users_mark
        )
        plotter.group_mass_intracomparison(
            groups_regions_mass.filter(user_group=group),
            mass_data.filter(user_group=group), regions, suffix=group,
            mark=config.users_mark, align=align_dm
        )
        for region in regions:
            plotter.named_dmdt_group_plot(
                region, group, rate_data.filter(user_group=group, basin_id=region)
            )
            plotter.named_dm_group_plot(
                region, group, mass_data.filter(user_group=group, basin_id=region),
                basis=groups_regions_mass.filter(user_group=group, basin_id=region).first()
            )
    # intercomparisons
    for _id, region in regions.items():
        reg = {_id: region}

        plotter.groups_rate_intercomparison(
            regions_rate.smooth(config.plot_smooth_window),
            groups_regions_rate.smooth(config.plot_smooth_window), reg
        )
        plotter.groups_mass_intercomparison(
            regions_mass, groups_regions_mass, reg, align=align_dm
        )
    # region comparisons
    ais_regions = [IceSheet.eais, IceSheet.wais, IceSheet.apis]
    all_regions = [IceSheet.ais, IceSheet.gris, IceSheet.all]

    plotter.regions_mass_intercomparison(
        regions_mass, *ais_regions
    )
    plotter.regions_mass_intercomparison(
        regions_mass, *all_regions
    )

    if not config.export_data:
        return

    # write data to files
    for region in regions:
        data = regions_rate.filter(basin_id=region).first()
        fname = os.path.join(output_path, region.value+".csv")

        print("exporting data:", fname, end="... ")
        with open(fname, 'w') as f:
            for line in zip(data.t, data.dmdt, data.errs):
                line = ", ".join(map(str, line)) + "\n"
                f.write(line)
        print("done.")
