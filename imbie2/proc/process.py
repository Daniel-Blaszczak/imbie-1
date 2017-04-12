from collections import OrderedDict

from imbie2.conf import ImbieConfig
from imbie2.const.basins import IceSheet, BasinGroup, ZwallyBasin, RignotBasin
from imbie2.const.error_methods import ErrorMethod
from imbie2.model.collections import WorkingMassRateCollection, MassChangeCollection, MassRateCollection
from imbie2.plot.plotter import Plotter
from imbie2.table.tables import MeanErrorsTable, TimeCoverageTable, BasinsTable


def process(input_data: MassRateCollection, config: ImbieConfig):

    groups = ["RA", "GMB", "IOM"]
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

    rate_data = input_data.chunk_series()

    # find users who have provided a full ice sheet of basin data, but no ice sheet series.
    # todo: this should probably be moved to a method of Collection, or similar.
    users = list({s.user for s in rate_data})
    for user in users:
        user_data = rate_data.filter(user=user)
        for group, basin_set in zip([BasinGroup.zwally, BasinGroup.rignot], [ZwallyBasin, RignotBasin]):
            for sheet in sheets:
                basins = list(basin_set.sheet(sheet))
                sheet_data = user_data.filter(basin_id=basins)

                if user_data.filter(basin_id=sheet, basin_group=group):
                    continue

                if len(sheet_data) == len(basins):
                    series = sheet_data.sum(error_method=ErrorMethod.rss)

                    series.basin_id = sheet
                    series.basin_group = group
                    series.user = user
                    series.aggregated = True

                    rate_data.add_series(series)

    zwally_data = rate_data.filter(basin_group=BasinGroup.zwally)
    rignot_data = rate_data.filter(basin_group=BasinGroup.rignot)

    rate_data.merge()

    mass_data = rate_data.integrate(offset=offset)

    groups_sheets_rate = WorkingMassRateCollection()
    groups_sheets_mass = MassChangeCollection()

    groups_regions_rate = WorkingMassRateCollection()
    groups_regions_mass = MassChangeCollection()

    sheets_rate = WorkingMassRateCollection()
    sheets_mass = MassChangeCollection()

    regions_rate = WorkingMassRateCollection()
    regions_mass = MassChangeCollection()

    for group in groups:
        for sheet in sheets:
            new_series = rate_data.filter(
                user_group=group, basin_id=sheet
            ).average(mode=config.combine_method)
            if new_series is None:
                continue

            groups_sheets_rate.add_series(new_series)
            groups_sheets_mass.add_series(
                new_series.integrate(offset=offset)
            )

        for region, sheets in regions.items():
            region_rate = groups_sheets_rate.filter(
                user_group=group, basin_id=sheets
            ).sum()
            if region_rate is None:
                continue

            region_rate.basin_id = region
            region_mass = region_rate.integrate(offset=offset)

            groups_regions_rate.add_series(region_rate)
            groups_regions_mass.add_series(region_mass)

    for sheet in sheets:
        sheet_rate_avg = groups_sheets_rate.filter(
            basin_id=sheet
        ).average(mode=config.combine_method)
        if sheet_rate_avg is None:
            continue

        sheets_rate.add_series(sheet_rate_avg)
        sheets_mass.add_series(
            sheet_rate_avg.integrate(offset=offset)
        )

    # compute region figures
    for region, sheets in regions.items():
        region_rate = sheets_rate.filter(
            basin_id=sheets
        ).sum()
        if region_rate is None:
            continue

        region_rate.basin_id = region

        regions_rate.add_series(region_rate)
        regions_mass.add_series(
            region_rate.integrate(offset=offset)
        )

    # print tables

    # met = MeanErrorsTable(rate_data)
    # f.write(met.get_html_string())
    # print(met)
    btz = BasinsTable(zwally_data, BasinGroup.zwally)
    # f.write(btz.get_html_string())
    print(btz)

    btr = BasinsTable(rignot_data, BasinGroup.rignot)
    # f.write(btr.get_html_string())
    print(btr)

    # for group in groups:
    #     tct = TimeCoverageTable(rate_data.filter(user_group=group))
    #     # f.write(tct.get_html_string())
    #     print(tct)

    # draw plots
    plotter = Plotter(
        filetype=config.plot_format,
        path=config.output_path,
        limits=True
    )
    # rignot/zwally comparison
    for sheet in sheets:
        plotter.rignot_zwally_comparison(
            rignot_data+zwally_data, [sheet]
        )
    # error bars (IMBIE1 style plot)
    plotter.sheets_error_bars(
        groups_regions_rate, regions_rate, groups, regions
    )
    # intracomparisons
    for group in groups:
        plotter.group_rate_boxes(
            rate_data.filter(user_group=group), {s: s for s in sheets}, suffix=group
        )
        plotter.group_rate_intracomparison(
            groups_regions_rate.filter(user_group=group),
            rate_data.filter(user_group=group), regions, suffix=group,
            mark=["Zwally", "Sandberg Sorensen", "Rietbroek"]
        )
        plotter.group_mass_intracomparison(
            groups_regions_mass.filter(user_group=group),
            mass_data.filter(user_group=group), regions, suffix=group,
            mark=["Zwally", "Sandberg Sorensen", "Rietbroek"]
        )
    # intercomparisons
    for _id, region in regions.items():
        reg = {_id: region}

        plotter.groups_rate_intercomparison(
            regions_rate, groups_regions_rate, reg
        )
        plotter.groups_mass_intercomparison(
            regions_mass, groups_regions_mass, reg
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