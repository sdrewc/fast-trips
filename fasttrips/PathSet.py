__copyright__ = "Copyright 2015-2016 Contributing Entities"
__license__   = """
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
"""
import collections,datetime,os,string,sys
import numpy,pandas

from .Error     import NotImplementedError, UnexpectedError
from .Logger    import FastTripsLogger
from .Passenger import Passenger
from .Route     import Route
from .TAZ       import TAZ
from .Transfer  import Transfer
from .Trip      import Trip
from .Util      import Util

#: Default user class: just one class called "all"
def generic_user_class(row_series):
    return "all"

class PathSet:
    """
    Represents a path set for a passenger from an origin :py:class:`TAZ` to a destination :py:class:`TAZ`
    through a set of stops.
    """
    #: Paths output file
    PATHS_OUTPUT_FILE               = 'ft_output_passengerPaths.txt'

    #: Path times output file
    PATH_TIMES_OUTPUT_FILE          = 'ft_output_passengerTimes.txt'

    #: Configured functions, indexed by name
    CONFIGURED_FUNCTIONS            = { 'generic_user_class':generic_user_class }

    #: Path configuration: Name of the function that defines user class
    USER_CLASS_FUNCTION             = None

    #: File with weights file.  Space delimited table.
    WEIGHTS_FILE                    = 'pathweight_ft.txt'
    #: Path weights
    WEIGHTS_DF                      = None

    #: Configuration: Minimum transfer penalty. Safeguard against having no transfer penalty
    #: which can result in terrible paths with excessive transfers.
    MIN_TRANSFER_PENALTY            = None
    #: Configuration: Overlap scale parameter.
    OVERLAP_SCALE_PARAMETER         = None
    #: Configuration: Overlap variable. Can be "None", "count", "distance", "time".
    OVERLAP_VARIABLE                = None
    #: Overlap variable option: None.  Don't use overlap pathsize correction.
    OVERLAP_NONE                    = "None"
    #: Overlap variable option: count. Use leg count overlap pathsize correction.
    OVERLAP_COUNT                   = "count"
    #: Overlap variable option: distance. Use leg distance overlap pathsize correction.
    OVERLAP_DISTANCE                = "distance"
    #: Overlap variable option: time. Use leg time overlap pathsize correction.
    OVERLAP_TIME                    = "time"
    #: Valid values for OVERLAP_VARAIBLE
    OVERLAP_VARIABLE_OPTIONS        = [OVERLAP_NONE,
                                       OVERLAP_COUNT,
                                       OVERLAP_DISTANCE,
                                       OVERLAP_TIME]

    #: Overlap option: Split transit leg into component parts?  e.g. split A-E
    #: into A-B-C-D-E for overlap calculations?
    OVERLAP_SPLIT_TRANSIT           = None

    #: Weights column: User Class
    WEIGHTS_COLUMN_USER_CLASS       = "user_class"
    #: Weights column: Purpose
    WEIGHTS_COLUMN_PURPOSE          = "purpose"
    #: Weights column: Demand Mode Type
    WEIGHTS_COLUMN_DEMAND_MODE_TYPE = "demand_mode_type"
    #: Weights column: Demand Mode Type
    WEIGHTS_COLUMN_DEMAND_MODE      = "demand_mode"
    #: Weights column: Supply Mode
    WEIGHTS_COLUMN_SUPPLY_MODE      = "supply_mode"
    #: Weights column: Weight Name
    WEIGHTS_COLUMN_WEIGHT_NAME      = "weight_name"
    #: Weights column: Weight Value
    WEIGHTS_COLUMN_WEIGHT_VALUE     = "weight_value"

    # ========== Added by fasttrips =======================================================
    #: Weights column: Supply Mode number
    WEIGHTS_COLUMN_SUPPLY_MODE_NUM  = "supply_mode_num"

    #: File with weights for c++
    OUTPUT_WEIGHTS_FILE             = "ft_intermediate_weights.txt"

    DIR_OUTBOUND    = 1  #: Trips outbound from home have preferred arrival times
    DIR_INBOUND     = 2  #: Trips inbound to home have preferred departure times

    PATH_KEY_COST           = "pf_cost"
    PATH_KEY_PROBABILITY    = "pf_probability"
    PATH_KEY_STATES         = "states"

    STATE_IDX_LABEL         = 0  #: :py:class:`datetime.timedelta` instance
    STATE_IDX_DEPARR        = 1  #: :py:class:`datetime.datetime` instance. Departure if outbound/backwards, arrival if inbound/forwards.
    STATE_IDX_DEPARRMODE    = 2  #: mode id
    STATE_IDX_TRIP          = 3  #: trip id
    STATE_IDX_SUCCPRED      = 4  #: stop identifier or TAZ identifier
    STATE_IDX_SEQ           = 5  #: sequence (for trip)
    STATE_IDX_SEQ_SUCCPRED  = 6  #: sequence for successor/predecessor
    STATE_IDX_LINKTIME      = 7  #: :py:class:`datetime.timedelta` instance
    STATE_IDX_COST          = 8  #: cost float, for hyperpath/stochastic assignment
    STATE_IDX_ARRDEP        = 9  #: :py:class:`datetime.datetime` instance. Arrival if outbound/backwards, departure if inbound/forwards.

    # these are also the demand_mode_type values
    STATE_MODE_ACCESS   = "access"
    STATE_MODE_EGRESS   = "egress"
    STATE_MODE_TRANSFER = "transfer"

    # new
    STATE_MODE_TRIP     = "transit" # onboard

    BUMP_EXPERIENCED_COST    = 999999
    HUGE_COST = 9999

    def __init__(self, trip_list_dict):
        """
        Constructor from dictionary mapping attribute to value.
        """
        self.__dict__.update(trip_list_dict)

        #: Direction is one of :py:attr:`PathSet.DIR_OUTBOUND` or :py:attr:`PathSet.DIR_INBOUND`
        #: Preferred time is a datetime.time object
        if trip_list_dict[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == "arrival":
            self.direction     = PathSet.DIR_OUTBOUND
            self.pref_time     = trip_list_dict[Passenger.TRIP_LIST_COLUMN_ARRIVAL_TIME].to_datetime().time()
            self.pref_time_min = trip_list_dict[Passenger.TRIP_LIST_COLUMN_ARRIVAL_TIME_MIN]
        elif trip_list_dict[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == "departure":
            self.direction     = PathSet.DIR_INBOUND
            self.pref_time     = trip_list_dict[Passenger.TRIP_LIST_COLUMN_DEPARTURE_TIME].to_datetime().time()
            self.pref_time_min = trip_list_dict[Passenger.TRIP_LIST_COLUMN_DEPARTURE_TIME_MIN]
        else:
            raise Exception("Don't understand trip_list %s: %s" % (Passenger.TRIP_LIST_COLUMN_TIME_TARGET, str(trip_list_dict)))

        #: Dict of path-num -> { cost:, probability:, states: [List of (stop_id, stop_state)]}
        self.pathdict = {}

    def goes_somewhere(self):
        """
        Does this path go somewhere?  Does the destination differ from the origin?
        """
        return (self.__dict__[Passenger.TRIP_LIST_COLUMN_ORIGIN_TAZ_ID] != self.__dict__[Passenger.TRIP_LIST_COLUMN_DESTINATION_TAZ_ID])

    def path_found(self):
        """
        Was a a transit path found from the origin to the destination with the constraints?
        """
        return len(self.pathdict) > 0

    def num_paths(self):
        """
        Number of paths in the PathSet
        """
        return len(self.pathdict)

    def reset(self):
        """
        Delete my states, something went wrong and it won't work out.
        """
        self.pathdict = []

    def outbound(self):
        """
        Quick accessor to see if :py:attr:`PathSet.direction` is :py:attr:`PathSet.DIR_OUTBOUND`.
        """
        return self.direction == PathSet.DIR_OUTBOUND

    @staticmethod
    def set_user_class(trip_list_df, new_colname):
        """
        Adds a column called user_class by applying the configured user class function.
        """
        trip_list_df[new_colname] = trip_list_df.apply(PathSet.CONFIGURED_FUNCTIONS[PathSet.USER_CLASS_FUNCTION], axis=1)

    @staticmethod
    def verify_weight_config(modes_df, output_dir, routes, capacity_constraint, trip_list_df):
        """
        Verify that we have complete weight configurations for the user classes and modes in the given DataFrame.

        Trips with invalid weight configurations will be dropped from the trip list and warned about.

        The parameter mode_df is a dataframe with the user_class, demand_mode_type and demand_mode combinations
        found in the demand file.

        If *capacity_constraint* is true, make sure there's an at_capacity weight on the transit supply mode links
        to enforce it.

        Returns updated trip_list_df.
        """
        error_str = ""
        # First, verify required columns are found
        weight_cols     = list(PathSet.WEIGHTS_DF.columns.values)
        FastTripsLogger.debug("verify_weight_config:\n%s" % PathSet.WEIGHTS_DF.to_string())
        assert(PathSet.WEIGHTS_COLUMN_USER_CLASS       in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_PURPOSE          in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_DEMAND_MODE      in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_SUPPLY_MODE      in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_WEIGHT_NAME      in weight_cols)
        assert(PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE     in weight_cols)

        # Join - make sure that all demand combinations (user class, purpose, demand mode type and demand mode) are configured
        weight_check = pandas.merge(left=modes_df,
                                    right=PathSet.WEIGHTS_DF,
                                    on=[PathSet.WEIGHTS_COLUMN_USER_CLASS,
                                        PathSet.WEIGHTS_COLUMN_PURPOSE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE],
                                    how='left')
        FastTripsLogger.debug("demand_modes x weights: \n%s" % weight_check.to_string())

        FastTripsLogger.debug("trip_list_df head=\n%s" % str(trip_list_df.head()))

        # If something is missing, warn and remove those trips
        null_supply_mode_weights = weight_check.loc[pandas.isnull(weight_check[PathSet.WEIGHTS_COLUMN_SUPPLY_MODE])]
        if len(null_supply_mode_weights) > 0:
            # warn
            FastTripsLogger.warn("The following user_class, demand_mode_type, demand_mode combinations exist in the demand file but are missing from the weight configuration:")
            FastTripsLogger.warn("\n%s" % null_supply_mode_weights.to_string())

            # remove those trips -- need to do it one demand mode type at a time
            null_supply_mode_weights = null_supply_mode_weights[[PathSet.WEIGHTS_COLUMN_USER_CLASS,
                                                                 PathSet.WEIGHTS_COLUMN_PURPOSE,
                                                                 PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                                                 PathSet.WEIGHTS_COLUMN_DEMAND_MODE]]
            null_supply_mode_weights["to_remove"] = 1
            for demand_mode_type in [PathSet.STATE_MODE_ACCESS, PathSet.STATE_MODE_EGRESS, PathSet.STATE_MODE_TRIP]:
                remove_trips = null_supply_mode_weights.loc[null_supply_mode_weights[PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE]==demand_mode_type].copy()
                if len(remove_trips) == 0: continue

                remove_trips.rename(columns={PathSet.WEIGHTS_COLUMN_DEMAND_MODE:"%s_mode" % demand_mode_type}, inplace=True)
                remove_trips.drop([PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE], axis=1, inplace=True)
                FastTripsLogger.debug("Removing for \n%s" % remove_trips)

                trip_list_df = pandas.merge(left  = trip_list_df,
                                            right = remove_trips,
                                            how   = "left")
                FastTripsLogger.debug("Removing\n%s" % trip_list_df.loc[pandas.notnull(trip_list_df["to_remove"])])

                # keep only those not flagged to_remove
                trip_list_df = trip_list_df.loc[pandas.isnull(trip_list_df["to_remove"])]
                trip_list_df.drop(["to_remove"], axis=1, inplace=True)


        # demand_mode_type and demand_modes implicit to all travel    :   xfer walk,  xfer wait, initial wait
        user_classes = modes_df[[PathSet.WEIGHTS_COLUMN_USER_CLASS, PathSet.WEIGHTS_COLUMN_PURPOSE]].drop_duplicates().reset_index()
        implicit_df = pandas.DataFrame({ PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE:[ 'transfer'],
                                         PathSet.WEIGHTS_COLUMN_DEMAND_MODE     :[ 'transfer'],
                                         PathSet.WEIGHTS_COLUMN_SUPPLY_MODE     :[ 'transfer'] })
        user_classes['key'] = 1
        implicit_df['key'] = 1
        implicit_df = pandas.merge(left=user_classes, right=implicit_df, on='key')
        implicit_df.drop(['index','key'], axis=1, inplace=True)
        # FastTripsLogger.debug("implicit_df: \n%s" % implicit_df)

        weight_check = pandas.merge(left=implicit_df, right=PathSet.WEIGHTS_DF,
                                    on=[PathSet.WEIGHTS_COLUMN_USER_CLASS,
                                        PathSet.WEIGHTS_COLUMN_PURPOSE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                                        PathSet.WEIGHTS_COLUMN_SUPPLY_MODE],
                                    how='left')
        FastTripsLogger.debug("implicit demand_modes x weights: \n%s" % weight_check.to_string())

        if pandas.isnull(weight_check[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]).sum() > 0:
            error_str += "\nThe following user_class, purpose, demand_mode_type, demand_mode, supply_mode combinations exist in the demand file but are missing from the weight configuration:\n"
            error_str += weight_check.loc[pandas.isnull(weight_check[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME])].to_string()
            error_str += "\n\n"

        # transfer penalty check
        tp_index = pandas.DataFrame({ PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE:['transfer'],
                                      PathSet.WEIGHTS_COLUMN_DEMAND_MODE     :['transfer'],
                                      PathSet.WEIGHTS_COLUMN_SUPPLY_MODE     :['transfer'],
                                      PathSet.WEIGHTS_COLUMN_WEIGHT_NAME     :['transfer_penalty']})
        uc_purp_index = PathSet.WEIGHTS_DF[[PathSet.WEIGHTS_COLUMN_USER_CLASS, PathSet.WEIGHTS_COLUMN_PURPOSE]].drop_duplicates()
        FastTripsLogger.debug("uc_purp_index: \n%s" % uc_purp_index)

        # these are all the transfer penalties we have
        transfer_penaltes = pandas.merge(left=tp_index, right=PathSet.WEIGHTS_DF, how='left')
        FastTripsLogger.debug("transfer_penaltes: \n%s" % transfer_penaltes)

        transfer_penalty_check = pandas.merge(left=uc_purp_index, right=transfer_penaltes, how='left')
        FastTripsLogger.debug("transfer_penalty_check: \n%s" % transfer_penalty_check)

        # missing transfer penalty
        if pandas.isnull(transfer_penalty_check[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]).sum() > 0:
            error_str += "\nThe following user class x purpose are missing a transfer penalty:\n"
            error_str += transfer_penalty_check.loc[pandas.isnull(transfer_penalty_check[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME])].to_string()
            error_str += "\n\n"

        bad_pen = transfer_penalty_check.loc[transfer_penalty_check[PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE] < PathSet.MIN_TRANSFER_PENALTY]
        if len(bad_pen) > 0:
            error_str += "\nThe following user class x purpose path weights have invalid (too small) transfer penalties. MIN=(%f)\n" % PathSet.MIN_TRANSFER_PENALTY
            error_str += bad_pen.to_string()
            error_str += "\nConfigure smaller min_transfer_penalty AT YOUR OWN RISK since this will make path generation slow/unreliable.\n\n"

        # If *capacity_constraint* is true, make sure there's an at_capacity weight on the transit supply mode links
        # to enforce it.
        if capacity_constraint:
            # see if it's here already -- we don't know how to handle that...
            at_capacity = PathSet.WEIGHTS_DF.loc[ PathSet.WEIGHTS_DF[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "at_capacity" ]
            if len(at_capacity) > 0:
                error_str += "\nFound at_capacity path weights explicitly set when about to set these for hard capacity constraints.\n"
                error_str += at_capacity.to_string()
                error_str += "\n\n"
            else:
                # set it for all user_class x transit x demand_mode x supply_mode
                transit_weights_df = PathSet.WEIGHTS_DF.loc[PathSet.WEIGHTS_DF[PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE] == PathSet.STATE_MODE_TRIP,
                    [PathSet.WEIGHTS_COLUMN_USER_CLASS,
                     PathSet.WEIGHTS_COLUMN_PURPOSE,
                     PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                     PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                     PathSet.WEIGHTS_COLUMN_SUPPLY_MODE]].copy()
                transit_weights_df.drop_duplicates(inplace=True)
                transit_weights_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME ] = "at_capacity"
                transit_weights_df[PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE] = PathSet.HUGE_COST
                FastTripsLogger.debug("Adding capacity-constraint weights:\n%s" % transit_weights_df.to_string())

                PathSet.WEIGHTS_DF = pandas.concat([PathSet.WEIGHTS_DF, transit_weights_df], axis=0)
                PathSet.WEIGHTS_DF.sort_values(by=[PathSet.WEIGHTS_COLUMN_USER_CLASS,
                                                   PathSet.WEIGHTS_COLUMN_PURPOSE,
                                                   PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                                   PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                                                   PathSet.WEIGHTS_COLUMN_SUPPLY_MODE,
                                                   PathSet.WEIGHTS_COLUMN_WEIGHT_NAME], inplace=True)

        if len(error_str) > 0:
            FastTripsLogger.fatal(error_str)
            sys.exit(2)

        # add mode numbers to weights DF for relevant rows
        PathSet.WEIGHTS_DF = routes.add_numeric_mode_id(PathSet.WEIGHTS_DF,
                                                    id_colname=PathSet.WEIGHTS_COLUMN_SUPPLY_MODE,
                                                    numeric_newcolname=PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM,
                                                    warn=True)  # don't fail if some supply modes are configured but not used, they may be for future runs
        FastTripsLogger.debug("PathSet weights: \n%s" % PathSet.WEIGHTS_DF)
        PathSet.WEIGHTS_DF.to_csv(os.path.join(output_dir,PathSet.OUTPUT_WEIGHTS_FILE),
                               columns=[PathSet.WEIGHTS_COLUMN_USER_CLASS,
                                        PathSet.WEIGHTS_COLUMN_PURPOSE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                                        PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM,
                                        PathSet.WEIGHTS_COLUMN_WEIGHT_NAME,
                                        PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE],
                               sep=" ", index=False)
        return trip_list_df

    def __str__(self):
        """
        Readable string version of the path.

        Note: If inbound trip, then the states are in reverse order (egress to access)
        """
        ret_str = "Dict vars:\n"
        for k,v in self.__dict__.iteritems():
            ret_str += "%30s => %-30s   %s\n" % (str(k), str(v), str(type(v)))
        # ret_str += PathSet.states_to_str(self.states, self.direction)
        return ret_str

    @staticmethod
    def write_paths(passengers_df, output_dir):
        """
        Write the assigned paths to the given output file.

        :param passengers_df: Passenger paths assignment results
        :type  passengers_df: :py:class:`pandas.DataFrame` instance
        :param output_dir:    Output directory
        :type  output_dir:    string

        """
        # get trip information -- board stops, board trips and alight stops
        passenger_trips = passengers_df.loc[passengers_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_TRIP].copy()
        ptrip_group     = passenger_trips.groupby([Passenger.PERSONS_COLUMN_PERSON_ID, Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM])
        # these are Series
        board_stops_str = ptrip_group.A_id.apply(lambda x:','.join(x))
        board_trips_str = ptrip_group.trip_id.apply(lambda x:','.join(x))
        alight_stops_str= ptrip_group.B_id.apply(lambda x:','.join(x))
        board_stops_str.name  = 'board_stop_str'
        board_trips_str.name  = 'board_trip_str'
        alight_stops_str.name = 'alight_stop_str'

        # get walking times
        walk_links = passengers_df.loc[(passengers_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_ACCESS  )| \
                                       (passengers_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_TRANSFER)| \
                                       (passengers_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_EGRESS  )].copy()
        walk_links['linktime_str'] = walk_links.pf_linktime.apply(lambda x: "%.2f" % (x/numpy.timedelta64(1,'m')))
        walklink_group = walk_links[['person_id','trip_list_id_num','linktime_str']].groupby(['person_id','trip_list_id_num'])
        walktimes_str  = walklink_group.linktime_str.apply(lambda x:','.join(x))

        # aggregate to one line per person_id, trip_list_id
        print_passengers_df = passengers_df[['person_id','trip_list_id_num','pathmode','A_id','B_id',Passenger.PF_COL_PAX_A_TIME]].groupby(['person_id','trip_list_id_num']).agg(
           {'pathmode'                  :'first',   # path mode
            'A_id'                      :'first',   # origin
            'B_id'                      :'last',    # destination
            Passenger.PF_COL_PAX_A_TIME :'first'    # start time
           })

        # put them all together
        print_passengers_df = pandas.concat([print_passengers_df,
                                            board_stops_str,
                                            board_trips_str,
                                            alight_stops_str,
                                            walktimes_str], axis=1)

        print_passengers_df.reset_index(inplace=True)
        print_passengers_df.sort_values(by=['trip_list_id_num'], inplace=True)

        print_passengers_df.rename(columns=
           {'pathmode'                  :'mode',
            'A_id'                      :'originTaz',
            'B_id'                      :'destinationTaz',
            Passenger.PF_COL_PAX_A_TIME :'startTime_time',
            'board_stop_str'            :'boardingStops',
            'board_trip_str'            :'boardingTrips',
            'alight_stop_str'           :'alightingStops',
            'linktime_str'              :'walkingTimes'}, inplace=True)

        print_passengers_df['startTime'] = print_passengers_df['startTime_time'].apply(Util.datetime64_formatter)

        print_passengers_df = print_passengers_df[['trip_list_id_num','person_id','mode','originTaz','destinationTaz','startTime',
                                                   'boardingStops','boardingTrips','alightingStops','walkingTimes']]

        print_passengers_df.to_csv(os.path.join(output_dir, PathSet.PATHS_OUTPUT_FILE), sep="\t", index=False)
        # passengerId mode    originTaz   destinationTaz  startTime   boardingStops   boardingTrips   alightingStops  walkingTimes

    @staticmethod
    def write_path_times(passengers_df, output_dir):
        """
        Write the assigned path times to the given output file.

        :param passengers_df: Passenger path links
        :type  passengers_df: :py:class:`pandas.DataFrame` instance
        :param output_dir:    Output directory
        :type  output_dir:    string
        """
        passenger_trips = passengers_df.loc[passengers_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_TRIP].copy()

        ######         TODO: this is really catering to output format; an alternative might be more appropriate
        from .Assignment import Assignment
        passenger_trips.loc[:,  'board_time_str'] = passenger_trips[Assignment.SIM_COL_PAX_BOARD_TIME ].apply(Util.datetime64_formatter)
        passenger_trips.loc[:,'arrival_time_str'] = passenger_trips[Passenger.PF_COL_PAX_A_TIME].apply(Util.datetime64_formatter)
        passenger_trips.loc[:, 'alight_time_str'] = passenger_trips[Assignment.SIM_COL_PAX_ALIGHT_TIME].apply(Util.datetime64_formatter)

        # Aggregate (by joining) across each passenger + path
        ptrip_group = passenger_trips.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                               Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM])
        # these are Series
        board_time_str   = ptrip_group['board_time_str'  ].apply(lambda x:','.join(x))
        arrival_time_str = ptrip_group['arrival_time_str'].apply(lambda x:','.join(x))
        alight_time_str  = ptrip_group['alight_time_str' ].apply(lambda x:','.join(x))

        # Aggregate other fields across each passenger + path
        pax_exp_df = passengers_df.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                            Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM]).agg(
            {# 'pathmode'                  :'first',  # path mode
             'A_id'                      :'first',  # origin
             'B_id'                      :'last',   # destination
             Passenger.PF_COL_PAX_A_TIME :'first',  # start time
             Passenger.PF_COL_PAX_B_TIME :'last',   # end time
             # TODO: cost needs to be updated for updated dwell & travel time
             # 'cost'                      :'first',  # total travel cost is calculated for the whole path
            })

        # Put them together and return
        assert(len(pax_exp_df) == len(board_time_str))
        pax_exp_df = pandas.concat([pax_exp_df,
                                    board_time_str,
                                    arrival_time_str,
                                    alight_time_str], axis=1)
        # print pax_exp_df.to_string(formatters={'A_time':Assignment.datetime64_min_formatter,
        #                                        'B_time':Assignment.datetime64_min_formatter})

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            simulated_person_ids = passengers_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].values

        for trace_pax in Assignment.TRACE_PERSON_IDS:
            if trace_pax not in simulated_person_ids:
                FastTripsLogger.debug("Passenger %s not in final simulated list" % trace_pax)
            else:
                FastTripsLogger.debug("Final passengers_df for %s\n%s" % \
                   (str(trace_pax),
                    passengers_df.loc[passengers_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID]==trace_pax].to_string(formatters=\
                   {Passenger.PF_COL_PAX_A_TIME :Util.datetime64_min_formatter,
                    Passenger.PF_COL_PAX_B_TIME :Util.datetime64_min_formatter,
                    Passenger.PF_COL_LINK_TIME  :Util.timedelta_formatter,
                    'board_time'           :Util.datetime64_min_formatter,
                    'alight_time'          :Util.datetime64_min_formatter,
                    'board_time_prev'      :Util.datetime64_min_formatter,
                    'alight_time_prev'     :Util.datetime64_min_formatter,
                    'B_time_prev'          :Util.datetime64_min_formatter,
                    'A_time_next'          :Util.datetime64_min_formatter,})))

                FastTripsLogger.debug("Passengers experienced times for %s\n%s" % \
                   (str(trace_pax),
                    pax_exp_df.loc[trace_pax].to_string(formatters=\
                   {Passenger.PF_COL_PAX_A_TIME :Util.datetime64_min_formatter,
                    Passenger.PF_COL_PAX_B_TIME :Util.datetime64_min_formatter})))

        # reset columns
        print_pax_exp_df = pax_exp_df.reset_index()
        print_pax_exp_df.sort_values(by=['trip_list_id_num'], inplace=True)

        print_pax_exp_df['A_time_str'] = print_pax_exp_df[Passenger.PF_COL_PAX_A_TIME].apply(Util.datetime64_formatter)
        print_pax_exp_df['B_time_str'] = print_pax_exp_df[Passenger.PF_COL_PAX_B_TIME].apply(Util.datetime64_formatter)

        # rename columns
        print_pax_exp_df.rename(columns=
            {#'pathmode'             :'mode',
             'A_id'                 :'originTaz',
             'B_id'                 :'destinationTaz',
             'A_time_str'           :'startTime',
             'B_time_str'           :'endTime',
             'arrival_time_str'     :'arrivalTimes',
             'board_time_str'       :'boardingTimes',
             'alight_time_str'      :'alightingTimes',
             # TODO: cost needs to be updated for updated dwell & travel time
             # 'cost'                 :'travelCost',
             }, inplace=True)

        # reorder
        print_pax_exp_df = print_pax_exp_df[[
            'trip_list_id_num',
            'person_id',
            #'mode',
            'originTaz',
            'destinationTaz',
            'startTime',
            'endTime',
            'arrivalTimes',
            'boardingTimes',
            'alightingTimes',
            # 'travelCost',
            ]]

        times_out = open(os.path.join(output_dir, PathSet.PATH_TIMES_OUTPUT_FILE), 'w')
        print_pax_exp_df.to_csv(times_out,
                                sep="\t", float_format="%.2f", index=False)

    @staticmethod
    def split_transit_links(pathset_links_df, veh_trips_df, stops):
        """
        Splits the transit links to their component links and returns.

        So if a transit trip goes from stop A to D but passes stop B and C in between, the
        row A->D will now be replaced by rows A->B, B->C, and C->D.

        Note that this does *not* renumber the linknum field.
        """
        from .Assignment import Assignment
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("split_transit_links: pathset_links_df (%d)\n%s" % (len(pathset_links_df),
                                  pathset_links_df.loc[pathset_links_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].to_string()))
            FastTripsLogger.debug("split_transit_links: pathset_links_df columns\n%s" % str(pathset_links_df.dtypes))

        veh_links_df = Trip.linkify_vehicle_trips(veh_trips_df, stops)
        veh_links_df["linkmode"] = "transit"

        FastTripsLogger.debug("split_transit_links: veh_links_df\n%s" % veh_links_df.head(20).to_string())

        # join the pathset links with the vehicle links
        path2 = pandas.merge(left    =pathset_links_df,
                             right   =veh_links_df,
                             on      =["linkmode","mode","mode_num",Trip.TRIPS_COLUMN_ROUTE_ID,Trip.TRIPS_COLUMN_TRIP_ID,Trip.TRIPS_COLUMN_TRIP_ID_NUM],
                             how     ="left",
                             suffixes=["","_veh"])
        # delete anything irrelevant -- so keep non-transit links, and transit links WITH valid sequences
        path2 = path2.loc[ (path2["linkmode"]!="transit") | ( (path2["linkmode"]=="transit") & (path2["A_seq_veh"]>=path2["A_seq"]) & (path2["B_seq_veh"]<=path2["B_seq"]) ) ]
        # These are the new columns -- incorporate them

        # A_arrival_time       datetime64[ns] => A time for intermediate links
        path2.loc[ (path2["linkmode"]=="transit")&(path2["A_id"]!=path2["A_id_veh"]), Assignment.SIM_COL_PAX_A_TIME     ] = path2["A_arrival_time"]
        # no waittime, boardtime, missed_xfer except on first link
        path2.loc[ (path2["linkmode"]=="transit")&(path2["A_id"]!=path2["A_id_veh"]), Assignment.SIM_COL_PAX_WAIT_TIME  ] = None
        path2.loc[ (path2["linkmode"]=="transit")&(path2["A_id"]!=path2["A_id_veh"]), Assignment.SIM_COL_PAX_BOARD_TIME ] = None
        path2.loc[ (path2["linkmode"]=="transit")&(path2["A_id"]!=path2["A_id_veh"]), Assignment.SIM_COL_PAX_MISSED_XFER] = 0
        # no alighttime except on last link
        path2.loc[ (path2["linkmode"]=="transit")&(path2["B_id"]!=path2["B_id_veh"]), Assignment.SIM_COL_PAX_ALIGHT_TIME] = None

        # route_id_num                float64 => ignore
        # A_id_veh                     object => A_id
        path2.loc[path2["linkmode"]=="transit", "A_id"       ] = path2["A_id_veh"]
        # A_id_num_veh                float64 => A_id_num
        path2.loc[path2["linkmode"]=="transit", "A_id_num"   ] = path2["A_id_num_veh"]
        # A_seq_veh                   float64 => A_seq
        path2.loc[path2["linkmode"]=="transit", "A_seq"      ] = path2["A_seq_veh"]
        # A_lat_veh                   float64 => A_lat
        path2.loc[path2["linkmode"]=="transit", "A_lat"      ] = path2["A_lat_veh"]
        # A_lon_veh                   float64 => A_lon
        path2.loc[path2["linkmode"]=="transit", "A_lon"      ] = path2["A_lon_veh"]

        # B_id_veh                     object => B_id
        path2.loc[path2["linkmode"]=="transit", "B_id"       ] = path2["B_id_veh"]
        # B_id_num_veh                float64 => B_id_num
        path2.loc[path2["linkmode"]=="transit", "B_id_num"   ] = path2["B_id_num_veh"]
        # B_seq_veh                   float64 => B_seq
        path2.loc[path2["linkmode"]=="transit", "B_seq"      ] = path2["B_seq_veh"]
        # B_arrival_time       datetime64[ns] => new_B_time
        path2.loc[path2["linkmode"]=="transit", "new_B_time" ] = path2["B_arrival_time"]
        # B_departure_time     datetime64[ns] => ignore
        # B_lat_veh                   float64 => B_lat
        path2.loc[path2["linkmode"]=="transit", "B_lat"      ] = path2["B_lat_veh"]
        # B_lon_veh                   float64 => B_lon
        path2.loc[path2["linkmode"]=="transit", "B_lon"      ] = path2["B_lon_veh"]

        # update the link time
        path2.loc[path2["linkmode"]=="transit",Assignment.SIM_COL_PAX_LINK_TIME] = path2[Assignment.SIM_COL_PAX_B_TIME] - path2[Assignment.SIM_COL_PAX_A_TIME]
        # update transit distance
        Util.calculate_distance_miles(path2, "A_lat","A_lon","B_lat","B_lon", "transit_distance")
        path2.loc[path2["linkmode"]=="transit",Assignment.SIM_COL_PAX_DISTANCE ] = path2["transit_distance"]

        # revert these back to ints
        path2[["A_id_num","B_id_num","A_seq","B_seq"]] = path2[["A_id_num","B_id_num","A_seq","B_seq"]].astype(int)

        # we're done with the fields - drop them
        path2.drop(["transit_distance", "route_id_num",
                    "A_id_veh","A_id_num_veh","A_seq_veh","A_arrival_time","A_departure_time","A_lat_veh","A_lon_veh",
                    "B_id_veh","B_id_num_veh","B_seq_veh","B_arrival_time","B_departure_time","B_lat_veh","B_lon_veh"], axis=1, inplace=True)

        # renumber linknum?  Let's not bother

        # trace
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("split_transit_links: path2 (%d)\n%s" % (len(path2),
                                  path2.loc[path2[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].to_string()))
        FastTripsLogger.debug("split_transit_links: path2 columns\n%s" % str(path2.dtypes))
        return path2

    @staticmethod
    def calculate_cost(iteration, simulation_iteration, STOCH_DISPERSION, pathset_paths_df, pathset_links_df, trip_list_df, transfers_df, walk_df, drive_df, veh_trips_df, stops):
        """
        This is equivalent to the C++ Path::calculateCost() method.  Would it be faster to do it in C++?
        It would require us to package up the networks and paths and send back and forth.  :p

        I think if we can do it using vectorized pandas operations, it should be fast, but we can compare/test.

        It's also messier to have this in two places.  Maybe we should delete it from the C++; the overlap calcs are only in here right now.

        Returns pathset_paths_df with additional column, Assignment.SIM_COL_PAX_COST, Assignment.SIM_COL_PAX_PROBABILITY, Assignment.SIM_COL_PAX_LOGSUM
        And pathset_links_df with additional column, Assignment.SIM_COL_PAX_COST

        """
        from .Assignment import Assignment

        # if these are here already, remove them since we'll recalculate them
        if Assignment.SIM_COL_PAX_COST in list(pathset_paths_df.columns.values):
            pathset_paths_df.drop([Assignment.SIM_COL_PAX_COST,
                                   Assignment.SIM_COL_PAX_LNPS,
                                   Assignment.SIM_COL_PAX_PROBABILITY,
                                   Assignment.SIM_COL_PAX_LOGSUM     ], axis=1, inplace=True)
            pathset_links_df.drop([Assignment.SIM_COL_PAX_COST       ], axis=1, inplace=True)

            # leaving this in for writing to CSV for debugging but I could take it out
            pathset_paths_df.drop(["logsum_component"], axis=1, inplace=True)


        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: pathset_links_df\n%s" % str(pathset_links_df.loc[pathset_links_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))
            FastTripsLogger.debug("calculate_cost: trip_list_df\n%s" % str(trip_list_df.loc[trip_list_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        pathset_links_to_use = pathset_links_df
        if PathSet.OVERLAP_SPLIT_TRANSIT:
            pathset_links_to_use = PathSet.split_transit_links(pathset_links_df, veh_trips_df, stops)

        # First, we need user class, purpose, and demand modes
        pathset_links_cost_df = pandas.merge(left =pathset_links_to_use,
                                             right=trip_list_df[[Passenger.PERSONS_COLUMN_PERSON_ID,
                                                                 Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                                                 Passenger.TRIP_LIST_COLUMN_USER_CLASS,
                                                                 Passenger.TRIP_LIST_COLUMN_PURPOSE,
                                                                 Passenger.TRIP_LIST_COLUMN_ACCESS_MODE,
                                                                 Passenger.TRIP_LIST_COLUMN_EGRESS_MODE,
                                                                 Passenger.TRIP_LIST_COLUMN_TRANSIT_MODE
                                                                ]],
                                             how  ="left",
                                             on   =[Passenger.PERSONS_COLUMN_PERSON_ID, Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM])
        # todo: add Value of time
        # Passenger.TRIP_LIST_COLUMN_VOT

        # linkmode = demand_mode_type.  Set demand_mode for the links
        pathset_links_cost_df[PathSet.WEIGHTS_COLUMN_DEMAND_MODE] = None
        pathset_links_cost_df.loc[ pathset_links_cost_df[Passenger.PF_COL_LINK_MODE]== PathSet.STATE_MODE_ACCESS  , PathSet.WEIGHTS_COLUMN_DEMAND_MODE] = pathset_links_cost_df[Passenger.TRIP_LIST_COLUMN_ACCESS_MODE ]
        pathset_links_cost_df.loc[ pathset_links_cost_df[Passenger.PF_COL_LINK_MODE]== PathSet.STATE_MODE_EGRESS  , PathSet.WEIGHTS_COLUMN_DEMAND_MODE] = pathset_links_cost_df[Passenger.TRIP_LIST_COLUMN_EGRESS_MODE ]
        pathset_links_cost_df.loc[ pathset_links_cost_df[Passenger.PF_COL_LINK_MODE]== PathSet.STATE_MODE_TRIP    , PathSet.WEIGHTS_COLUMN_DEMAND_MODE] = pathset_links_cost_df[Passenger.TRIP_LIST_COLUMN_TRANSIT_MODE]
        pathset_links_cost_df.loc[ pathset_links_cost_df[Passenger.PF_COL_LINK_MODE]== PathSet.STATE_MODE_TRANSFER, PathSet.WEIGHTS_COLUMN_DEMAND_MODE] = "transfer"
        # Verify that it's set for every link
        missing_demand_mode = pandas.isnull(pathset_links_cost_df[PathSet.WEIGHTS_COLUMN_DEMAND_MODE]).sum()
        assert(missing_demand_mode == 0)

        # drop the individual mode columns, we have what we need
        pathset_links_cost_df.drop([Passenger.TRIP_LIST_COLUMN_ACCESS_MODE,
                                    Passenger.TRIP_LIST_COLUMN_EGRESS_MODE,
                                    Passenger.TRIP_LIST_COLUMN_TRANSIT_MODE], axis=1, inplace=True)

        # if this isn't set yet (only simulation_iteration==0) set it
        if simulation_iteration == 0:
            pathset_links_cost_df[Assignment.SIM_COL_PAX_BUMP_ITER] = -1

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: pathset_links_cost_df\n%s" % str(pathset_links_cost_df.loc[pathset_links_cost_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        # Inner join with the weights - now each weight has a row
        cost_df = pandas.merge(left    =pathset_links_cost_df,
                               right   =PathSet.WEIGHTS_DF,
                               # TODO: add purpose
                               left_on =[Passenger.TRIP_LIST_COLUMN_USER_CLASS,
                                         Passenger.TRIP_LIST_COLUMN_PURPOSE,
                                         Passenger.PF_COL_LINK_MODE,
                                         PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                                         Passenger.TRIP_LIST_COLUMN_MODE],
                               right_on=[Passenger.TRIP_LIST_COLUMN_USER_CLASS,
                                         Passenger.TRIP_LIST_COLUMN_PURPOSE,
                                         PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                                         PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                                         PathSet.WEIGHTS_COLUMN_SUPPLY_MODE],
                               how     ="inner")

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: cost_df\n%s" % str(cost_df.loc[cost_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].sort_values([Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,Passenger.PF_COL_PATH_NUM,Passenger.PF_COL_LINK_NUM]).head(20)))

        # NOW we split it into 3 lists -- access/egress, transit, and transfer
        # This is because they will each be joined to tables specific to those kinds of mode categories, and so we don't want all the transit nulls on the other tables, etc.
        cost_columns = list(cost_df.columns.values)
        cost_df["var_value"] = numpy.nan  # This means unset
        cost_accegr_df       = cost_df.loc[(cost_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_ACCESS  )|(cost_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_EGRESS)]
        cost_trip_df         = cost_df.loc[(cost_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_TRIP    )]
        cost_transfer_df     = cost_df.loc[(cost_df[Passenger.PF_COL_LINK_MODE]==PathSet.STATE_MODE_TRANSFER)]
        del cost_df

        ##################### First, handle Access/Egress link costs

        for accegr_type in ["walk","bike","drive"]:

            # make copies; we don't want to mess with originals
            if accegr_type == "walk":
                link_df   = walk_df.copy()
                mode_list = TAZ.WALK_MODE_NUMS
            elif accegr_type == "bike":
                mode_list = TAZ.BIKE_MODE_NUMS
                # not supported yet
                continue
            else:
                link_df   = drive_df.copy()
                mode_list = TAZ.DRIVE_MODE_NUMS

            FastTripsLogger.debug("Access/egress link_df %s\n%s" % (accegr_type, link_df.head().to_string()))
            if len(link_df) == 0:
                continue

            # format these with A & B instead of TAZ and Stop
            link_df.reset_index(inplace=True)
            link_df["A_id_num"] = -1
            link_df["B_id_num"] = -1
            link_df.loc[link_df[TAZ.WALK_ACCESS_COLUMN_SUPPLY_MODE_NUM].isin(TAZ.ACCESS_MODE_NUMS), "A_id_num"] = link_df[TAZ.WALK_ACCESS_COLUMN_TAZ_NUM ]
            link_df.loc[link_df[TAZ.WALK_ACCESS_COLUMN_SUPPLY_MODE_NUM].isin(TAZ.ACCESS_MODE_NUMS), "B_id_num"] = link_df[TAZ.WALK_ACCESS_COLUMN_STOP_NUM]
            link_df.loc[link_df[TAZ.WALK_ACCESS_COLUMN_SUPPLY_MODE_NUM].isin(TAZ.EGRESS_MODE_NUMS), "A_id_num"] = link_df[TAZ.WALK_ACCESS_COLUMN_STOP_NUM]
            link_df.loc[link_df[TAZ.WALK_ACCESS_COLUMN_SUPPLY_MODE_NUM].isin(TAZ.EGRESS_MODE_NUMS), "B_id_num"] = link_df[TAZ.WALK_ACCESS_COLUMN_TAZ_NUM ]
            link_df.drop([TAZ.WALK_ACCESS_COLUMN_TAZ_NUM, TAZ.WALK_ACCESS_COLUMN_STOP_NUM], axis=1, inplace=True)
            assert(len(link_df.loc[link_df["A_id_num"] < 0]) == 0)

            FastTripsLogger.debug("%s link_df =\n%s" % (accegr_type, link_df.head().to_string()))

            # Merge access/egress with walk|bike|drive access/egress information
            cost_accegr_df = pandas.merge(left     = cost_accegr_df,
                                          right    = link_df,
                                          on       = ["A_id_num",
                                                      PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM,
                                                      "B_id_num"],
                                          how      = "left")
            # rename new columns so it's clear it's for walk|bike|drive
            for colname in list(link_df.select_dtypes(include=['float64','int64']).columns.values):
                # don't worry about join columns
                if colname in ["A_id_num", PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM, "B_id_num"]: continue

                # rename the rest
                new_colname = "%s %s" % (colname, accegr_type)
                cost_accegr_df.rename(columns={colname:new_colname}, inplace=True)

                # use it, if relevant
                cost_accegr_df.loc[ (cost_accegr_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == colname)&
                                    (cost_accegr_df[PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM].isin(mode_list)), "var_value"] = cost_accegr_df[new_colname]

        # Access/egress needs passenger trip departure, arrival and time_target
        cost_accegr_df = pandas.merge(left =cost_accegr_df,
                                      right=trip_list_df[[Passenger.PERSONS_COLUMN_PERSON_ID,
                                                          Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                                          Passenger.TRIP_LIST_COLUMN_DEPARTURE_TIME,
                                                          Passenger.TRIP_LIST_COLUMN_ARRIVAL_TIME,
                                                          Passenger.TRIP_LIST_COLUMN_TIME_TARGET,
                                                        ]],
                                      how  ="left",
                                      on   =[Passenger.PERSONS_COLUMN_PERSON_ID, Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM])

        # preferred delay_min - arrival means want to arrive before that time
        cost_accegr_df.loc[(cost_accegr_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]     == "preferred_delay_min"    )& \
                           (cost_accegr_df[Passenger.PF_COL_LINK_MODE]             == PathSet.STATE_MODE_ACCESS)& \
                           (cost_accegr_df[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == 'arrival'), "var_value"] = 0.0
        cost_accegr_df.loc[(cost_accegr_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]     == "preferred_delay_min"    )& \
                           (cost_accegr_df[Passenger.PF_COL_LINK_MODE]             == PathSet.STATE_MODE_EGRESS)& \
                           (cost_accegr_df[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == 'arrival'), "var_value"] = (cost_accegr_df[Passenger.TRIP_LIST_COLUMN_ARRIVAL_TIME] - cost_accegr_df[Passenger.PF_COL_PAX_B_TIME])/numpy.timedelta64(1,'m')
        # preferred delay_min - departure means want to depart after that time
        cost_accegr_df.loc[(cost_accegr_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]     == "preferred_delay_min"    )& \
                           (cost_accegr_df[Passenger.PF_COL_LINK_MODE]             == PathSet.STATE_MODE_ACCESS)& \
                           (cost_accegr_df[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == 'departure'), "var_value"] = (cost_accegr_df[Passenger.PF_COL_PAX_A_TIME] - cost_accegr_df[Passenger.TRIP_LIST_COLUMN_DEPARTURE_TIME])/numpy.timedelta64(1,'m')
        cost_accegr_df.loc[(cost_accegr_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME]     == "preferred_delay_min"    )& \
                           (cost_accegr_df[Passenger.PF_COL_LINK_MODE]             == PathSet.STATE_MODE_EGRESS)& \
                           (cost_accegr_df[Passenger.TRIP_LIST_COLUMN_TIME_TARGET] == 'departure'), "var_value"] = 0.0

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("cost_accegr_df=\n%s\ndtypes=\n%s" % (cost_accegr_df.loc[cost_accegr_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].to_string(), str(cost_accegr_df.dtypes)))

        missing_accegr_costs = cost_accegr_df.loc[ pandas.isnull(cost_accegr_df["var_value"]) ]
        error_accegr_msg = "Missing %d out of %d access/egress var_value values" % (len(missing_accegr_costs), len(cost_accegr_df))
        FastTripsLogger.debug(error_accegr_msg)

        if len(missing_accegr_costs) > 0:
            error_accegr_msg += "\n%s" % missing_accegr_costs.head(10).to_string()
            FastTripsLogger.fatal(error_accegr_msg)

        ##################### Next, handle Transit Trip link costs
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("cost_trip_df=\n%s\ndtypes=\n%s" % (cost_trip_df.loc[cost_trip_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].to_string(), str(cost_trip_df.dtypes)))

        # if there's a board time, in_vehicle_time = new_B_time - board_time
        #               otherwise, in_vehicle_time = B time - A time (for when we split)
        cost_trip_df.loc[(cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "in_vehicle_time_min")&pandas.notnull(cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME]), "var_value"] = \
            (cost_trip_df[Assignment.SIM_COL_PAX_B_TIME] - cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME])/numpy.timedelta64(1,'m')
        cost_trip_df.loc[(cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "in_vehicle_time_min")& pandas.isnull(cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME]), "var_value"] = \
            (cost_trip_df[Assignment.SIM_COL_PAX_B_TIME] - cost_trip_df[Assignment.SIM_COL_PAX_A_TIME])/numpy.timedelta64(1,'m')

        # if in vehicle time is less than 0 then off by 1 day error
        cost_trip_df.loc[(cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "in_vehicle_time_min")&(cost_trip_df["var_value"]<0), "var_value"] = cost_trip_df["var_value"] + (24*60)

        # if there's a board time, wait time = board_time - A time
        #               otherwise, wait time = 0 (for when we split transit links)
        cost_trip_df.loc[(cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "wait_time_min")&pandas.notnull(cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME]), "var_value"] = \
            (cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME] - cost_trip_df[Assignment.SIM_COL_PAX_A_TIME])/numpy.timedelta64(1,'m')
        cost_trip_df.loc[(cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "wait_time_min")& pandas.isnull(cost_trip_df[Assignment.SIM_COL_PAX_BOARD_TIME]), "var_value"] = 0

        # which overcap column to use?
        overcap_col = Trip.SIM_COL_VEH_OVERCAP
        if Assignment.MSA_RESULTS and Trip.SIM_COL_VEH_MSA_OVERCAP in list(cost_trip_df.columns.values): overcap_col = Trip.SIM_COL_VEH_MSA_OVERCAP

        # at cap is a binary, 1 if overcap >= 0 and they're not one of the lucky few that boarded
        cost_trip_df["at_capacity"] = 0.0
        if Assignment.SIM_COL_PAX_BUMPSTOP_BOARDED in list(cost_trip_df.columns.values):
            cost_trip_df.loc[ (cost_trip_df[overcap_col] >= 0)&(cost_trip_df[Assignment.SIM_COL_PAX_BUMPSTOP_BOARDED] != 1), "at_capacity" ] = 1.0
        else:
            cost_trip_df.loc[ (cost_trip_df[overcap_col] >= 0)                                                             , "at_capacity" ] = 1.0

        cost_trip_df.loc[cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "at_capacity"    , "var_value"] = cost_trip_df["at_capacity"]
        cost_trip_df.loc[cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "overcap"        , "var_value"] = cost_trip_df[overcap_col]
        # overcap shouldn't be negative
        cost_trip_df.loc[ (cost_trip_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "overcap")&(cost_trip_df["var_value"]<0), "var_value"] = 0.0

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("cost_trip_df=\n%s\ndtypes=\n%s" % (cost_trip_df.loc[cost_trip_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)].to_string(), str(cost_trip_df.dtypes)))

        missing_trip_costs = cost_trip_df.loc[ pandas.isnull(cost_trip_df["var_value"]) ]
        error_trip_msg = "Missing %d out of %d transit trip var_value values" % (len(missing_trip_costs), len(cost_trip_df))
        FastTripsLogger.debug(error_trip_msg)

        if len(missing_trip_costs) > 0:
            error_trip_msg += "\n%s" % missing_trip_costs.head(10).to_string()
            FastTripsLogger.fatal(error_trip_msg)

        ##################### Finally, handle Transfer link costs
        FastTripsLogger.debug("cost_transfer_df head = \n%s\ntransfers_df head=\n%s" % (cost_transfer_df.head().to_string(), transfers_df.head().to_string()))
        cost_transfer_df = pandas.merge(left     = cost_transfer_df,
                                        left_on  = ["A_id_num","B_id_num"],
                                        right    = transfers_df,
                                        right_on = [Transfer.TRANSFERS_COLUMN_FROM_STOP_NUM, Transfer.TRANSFERS_COLUMN_TO_STOP_NUM],
                                        how      = "left")
        cost_transfer_df.loc[cost_transfer_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "walk_time_min"   , "var_value"] = cost_transfer_df[Passenger.PF_COL_LINK_TIME]/numpy.timedelta64(1,'m')

        # any numeric column can be used
        for colname in list(transfers_df.select_dtypes(include=['float64','int64']).columns.values):
            FastTripsLogger.debug("Using numeric column %s" % colname)
            cost_transfer_df.loc[cost_transfer_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == colname, "var_value"] = cost_transfer_df[colname]

        # make zero walk transfers have default var_values 0
        cost_transfer_df.loc[ (cost_transfer_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] != "transfer_penalty")&
                              (cost_transfer_df["A_id_num"]==cost_transfer_df["B_id_num"]), "var_value"] = 0.0
        # zero walk transfers have a transfer penalty although they're not otherwise configured
        cost_transfer_df.loc[ (cost_transfer_df[PathSet.WEIGHTS_COLUMN_WEIGHT_NAME] == "transfer_penalty")&
                              (pandas.isnull(cost_transfer_df["var_value"])), "var_value"] = 1.0

        # FastTripsLogger.debug("cost_transfer_df=\n%s\ndtypes=\n%s" % (cost_transfer_df.head().to_string(), str(cost_transfer_df.dtypes)))

        missing_transfer_costs = cost_transfer_df.loc[ pandas.isnull(cost_transfer_df["var_value"]) ]
        error_transfer_msg = "Missing %d out of %d transfer var_value values" % (len(missing_transfer_costs), len(cost_transfer_df))
        FastTripsLogger.debug(error_transfer_msg)

        if len(missing_transfer_costs) > 0:
            error_transfer_msg += "\n%s" % missing_transfer_costs.head(10).to_string()
            FastTripsLogger.fatal(error_transfer_msg)

        # abort here if we're missing anything
        if len(missing_accegr_costs) + len(missing_trip_costs) + len(missing_transfer_costs) > 0:
            raise NotImplementedError("Missing var_values; See log")

        ##################### Put them back together into a single dataframe
        cost_columns = [Passenger.PERSONS_COLUMN_PERSON_ID,
                        Passenger.TRIP_LIST_COLUMN_USER_CLASS,
                        Passenger.TRIP_LIST_COLUMN_PURPOSE,
                        Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                        Passenger.PF_COL_PATH_NUM,
                        Passenger.PF_COL_LINK_NUM,
                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE_TYPE,
                        PathSet.WEIGHTS_COLUMN_DEMAND_MODE,
                        PathSet.WEIGHTS_COLUMN_SUPPLY_MODE,
                        PathSet.WEIGHTS_COLUMN_SUPPLY_MODE_NUM,
                        PathSet.WEIGHTS_COLUMN_WEIGHT_NAME,
                        PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE,
                        "var_value",
                        Assignment.SIM_COL_MISSED_XFER,
                        Assignment.SIM_COL_PAX_BUMP_ITER]
        cost_accegr_df   = cost_accegr_df[cost_columns]
        cost_trip_df     = cost_trip_df[cost_columns]
        cost_transfer_df = cost_transfer_df[cost_columns]
        cost_df          = pandas.concat([cost_accegr_df, cost_trip_df, cost_transfer_df], axis=0)

        # FastTripsLogger.debug("calculate_cost: cost_df=\n%s\ndtypes=\n%s" % (cost_df.to_string(), str(cost_df.dtypes)))

        # linkcost = weight x variable
        cost_df[Assignment.SIM_COL_PAX_COST] = cost_df["var_value"]*cost_df[PathSet.WEIGHTS_COLUMN_WEIGHT_VALUE]

        # TODO: option: make these more subtle?
        # missed_xfer has huge cost
        cost_df.loc[cost_df[Assignment.SIM_COL_MISSED_XFER  ]==1, Assignment.SIM_COL_PAX_COST] = PathSet.HUGE_COST
        # bump iter means over capacity
        cost_df.loc[cost_df[Assignment.SIM_COL_PAX_BUMP_ITER]>=0, Assignment.SIM_COL_PAX_COST] = PathSet.HUGE_COST

        cost_df.sort_values([Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                             Passenger.PF_COL_PATH_NUM,
                             Passenger.PF_COL_LINK_NUM], inplace=True)
        FastTripsLogger.debug("calculate_cost: cost_df\n%s" % str(cost_df.loc[cost_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        # verify all costs are non-negative
        if cost_df[Assignment.SIM_COL_PAX_COST].min() < 0:
            msg = "calculate_cost: Negative costs found:\n%s" % cost_df.loc[ cost_df[Assignment.SIM_COL_PAX_COST]<0 ].to_string()
            FastTripsLogger.fatal(msg)
            raise UnexpectedError(msg)

        ###################### sum linkcost to links
        cost_link_df = cost_df[[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                Passenger.PF_COL_PATH_NUM,
                                Passenger.PF_COL_LINK_NUM,
                                Assignment.SIM_COL_PAX_COST]].groupby(
                                   [Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                    Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                    Passenger.PF_COL_PATH_NUM,
                                    Passenger.PF_COL_LINK_NUM]).aggregate('sum').reset_index()
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: cost_link_df\n%s" % str(cost_link_df.loc[cost_link_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))
        # join to pathset_links_df
        pathset_links_df = pandas.merge(left =pathset_links_df,
                                        right=cost_link_df,
                                        how  ="left",
                                        on   =[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                               Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                               Passenger.PF_COL_PATH_NUM,
                                               Passenger.PF_COL_LINK_NUM])
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: pathset_links_df\n%s" % str(pathset_links_df.loc[pathset_links_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        ###################### overlap calcs
        overlap_df = None
        if PathSet.OVERLAP_VARIABLE != PathSet.OVERLAP_NONE:

            # CHUNKING because we run into memory problems
            # TODO: figure out more sophisticated chunk size
            CHUNK_SIZE = 1000 # person trips
            chunk_list = pathset_links_to_use[[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                               Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM]].drop_duplicates().reset_index(drop=True)
            num_chunks = len(chunk_list)/CHUNK_SIZE + 1
            chunk_list["chunk_num"] = numpy.floor_divide(chunk_list.index, CHUNK_SIZE)
            FastTripsLogger.debug("calculate_cost: chunk_list size=%d head=\n%s\ntail=\n%s" % (len(chunk_list), chunk_list.head().to_string(), chunk_list.tail().to_string()))
            pathset_links_to_use = pandas.merge(left  =pathset_links_to_use,
                                                right =chunk_list,
                                                how   ='left')
            FastTripsLogger.debug("calculate_cost: mem_use=%s pathset_links_to_use has length %d, head=\n%s" % (Util.get_process_mem_use_str(), 
                                  len(pathset_links_to_use), pathset_links_to_use.head().to_string()))
            full_overlap_df = pandas.DataFrame()

            for chunk_num in range(num_chunks):

                # get the person trips in the chunk
                overlap_df = pathset_links_to_use.loc[ pathset_links_to_use["chunk_num"] == chunk_num]

                overlap_df = overlap_df[[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                         Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                         Passenger.PF_COL_PATH_NUM,
                                         Passenger.PF_COL_LINK_NUM,
                                         "A_id_num","B_id_num",
                                         Route.ROUTES_COLUMN_MODE_NUM,
                                         "new_linktime",
                                         Assignment.SIM_COL_PAX_DISTANCE]].copy()
                # sum count, time, dist(TODO) to path and add path sum version to overlap_df -- this is L
                FastTripsLogger.debug("calculate_cost: mem_use=%s overlap_df has length %d, head=\n%s" % (Util.get_process_mem_use_str(), len(overlap_df), overlap_df.head().to_string()))

                # path aggregate
                overlap_df["count"] = 1
                overlap_path_df = overlap_df.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                                      Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                                      Passenger.PF_COL_PATH_NUM]).aggregate({'count':'sum','new_linktime':'sum',Assignment.SIM_COL_PAX_DISTANCE:'sum'}).reset_index(drop=False)
                overlap_path_df.rename(columns={"count":"path_count", "new_linktime":"path_time", Assignment.SIM_COL_PAX_DISTANCE:"path_distance"}, inplace=True)
                overlap_df.drop(["count"], axis=1, inplace=True)

                FastTripsLogger.debug("calculate_cost: mem_use=%s overlap_path_df has length %d, head=\n%s" % (Util.get_process_mem_use_str(), len(overlap_path_df), overlap_path_df.head().to_string()))

                # get the path variables
                overlap_df = pandas.merge(overlap_df, overlap_path_df, how="left",
                                          on=[Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,Passenger.PF_COL_PATH_NUM])
                del overlap_path_df

                # outer join on trip_list_id_num means when they match, we'll get a cartesian product of the links
                overlap_df = pandas.merge(overlap_df, overlap_df.copy(), on=[Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM], how="outer")
                FastTripsLogger.debug("calculate_cost: mem_use=%s overlap_df has length %d, head=\n%s" % (Util.get_process_mem_use_str(), len(overlap_df), overlap_df.head().to_string()))

                # count matches -- matching A,B,mode
                overlap_df["match"] = 0
                overlap_df.loc[ (overlap_df["A_id_num_x"]==overlap_df["A_id_num_y"])&
                                (overlap_df["B_id_num_x"]==overlap_df["B_id_num_y"])&
                                (overlap_df["mode_num_x"]==overlap_df["mode_num_y"])  , "match"] = 1

                if PathSet.OVERLAP_VARIABLE == PathSet.OVERLAP_COUNT:
                    overlap_df["link_prop_x"] = 1.0/overlap_df["path_count_x"]                         # l_a/L_i
                    overlap_df["pathlen_x_y"] = overlap_df["path_count_x"]/overlap_df["path_count_y"]  # L_i/L_j
                elif PathSet.OVERLAP_VARIABLE == PathSet.OVERLAP_TIME:
                    overlap_df["link_prop_x"] = overlap_df["new_linktime_x"]/overlap_df["path_time_x"] # l_a/L_i
                    overlap_df["pathlen_x_y"] = overlap_df["path_time_x"]/overlap_df["path_time_y"]    # L_i/L_j
                elif PathSet.OVERLAP_VARIABLE == PathSet.OVERLAP_DISTANCE:
                    overlap_df["link_prop_x"] = overlap_df["distance_x"]/overlap_df["path_distance_x"] # l_a/L_i
                    overlap_df["pathlen_x_y"] = overlap_df["path_distance_x"]/overlap_df["path_distance_y"]    # L_i/L_j

                overlap_df["pathlen_x_y_scale"] = overlap_df[["pathlen_x_y"]].pow(PathSet.OVERLAP_SCALE_PARAMETER)  # (L_i/L_j)^gamma
                # zero it out if it's not a match
                overlap_df.loc[overlap_df["match"]==0, "pathlen_x_y_scale"] = 0
                # now pathlen_x_y_scale = (L_i/L_j)^gamma x delta_aj

                if len(Assignment.TRACE_PERSON_IDS) > 0:
                    FastTripsLogger.debug("calculate_cost: overlap_df\n%s" % str(overlap_df.loc[overlap_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

                # debug
                # overlap_df_temp = overlap_df.groupby([Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM, "pathnum_x","linknum_x","link_prop_x","pathnum_y"]).aggregate({"match":"sum", "pathlen_x_y_scale":"sum"})
                # FastTripsLogger.debug("calculate_cost: overlap_df_temp\n%s" % str(overlap_df_temp.head(50)))

                # group by pathnum_x, linknum_x -- so this sums over paths P_j in equation (or pathnum_y here)
                overlap_df = overlap_df.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM, "pathnum_x","linknum_x","link_prop_x"]).aggregate({"pathlen_x_y_scale":"sum"}).reset_index()
                # now pathlen_x_y_scale = SUM_j (L_i/L_j)^gamma x delta_aj
                overlap_df["PS"] = overlap_df["link_prop_x"]/overlap_df["pathlen_x_y_scale"]  # l_a/L_i * 1/(SUM_j (L_i/L_j)^gamma x delta_aj)
                if len(Assignment.TRACE_PERSON_IDS) > 0:
                    FastTripsLogger.debug("calculate_cost: overlap_df\n%s" % str(overlap_df.loc[overlap_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

                # sum across link in path
                overlap_df = overlap_df.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM, "pathnum_x"]).aggregate({"PS":"sum"}).reset_index(drop=False)

                # Check all pathsizes are in [0,1]
                min_PS = overlap_df["PS"].min()
                max_PS = overlap_df["PS"].max()
                FastTripsLogger.debug("PathSize min=%f max=%f" % (min_PS, max_PS))
                if min_PS < 0:
                    FastTripsLogger.fatal("Min pathsize = %f < 0:\n%s" % (min_PS, overlap_df.loc[overlap_df["PS"]==min_PS].to_string()))
                if max_PS > 1.0001:
                    FastTripsLogger.fatal("Max pathsize = %f > 1:\n%s" % (max_PS, overlap_df.loc[overlap_df["PS"]==max_PS].to_string()))

                overlap_df[Assignment.SIM_COL_PAX_LNPS] = numpy.log(overlap_df["PS"])
                if len(Assignment.TRACE_PERSON_IDS) > 0:
                    FastTripsLogger.debug("calculate_cost: overlap_df\n%s" % str(overlap_df.loc[overlap_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

                # rename pathnum_x to pathnum and drop PS.  Now overlap_df has columns trip_list_id_num, pathnum, ln_PS
                overlap_df.rename(columns={"pathnum_x":Passenger.PF_COL_PATH_NUM}, inplace=True)
                overlap_df.drop(["PS"], axis=1, inplace=True) # we have ln_PS

                if len(full_overlap_df) == 0:
                    full_overlap_df = overlap_df
                else:
                    full_overlap_df = full_overlap_df.append(overlap_df)
                FastTripsLogger.debug("calculate_cost: mem_use=%s full_overlap_df has length %d" % (Util.get_process_mem_use_str(), len(full_overlap_df)))

        ###################### sum linkcost to paths
        cost_link_df.drop([Passenger.PF_COL_LINK_NUM], axis=1, inplace=True)
        cost_path_df = cost_link_df.groupby([Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,Passenger.PF_COL_PATH_NUM]).aggregate('sum').reset_index()
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: cost_path_df\n%s" % str(cost_path_df.loc[cost_path_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))
        # join to pathset_paths_df
        pathset_paths_df = pandas.merge(left =pathset_paths_df,
                                        right=cost_path_df,
                                        how  ="left",
                                        on   =[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                               Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                               Passenger.PF_COL_PATH_NUM])

        if PathSet.OVERLAP_VARIABLE == PathSet.OVERLAP_NONE:
            pathset_paths_df[Assignment.SIM_COL_PAX_LNPS] = 0
        else:
            pathset_paths_df = pandas.merge(left =pathset_paths_df,
                                            right=full_overlap_df,
                                            how  ="left",
                                            on   =[Passenger.TRIP_LIST_COLUMN_PERSON_ID,
                                                   Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM,
                                                   Passenger.PF_COL_PATH_NUM])
        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: pathset_paths_df\n%s" % str(pathset_paths_df.loc[pathset_paths_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        ###################### logsum and probabilities
        pathset_paths_df["logsum_component"] = numpy.exp((-1.0*STOCH_DISPERSION)*(pathset_paths_df[Assignment.SIM_COL_PAX_COST] + pathset_paths_df[Assignment.SIM_COL_PAX_LNPS]))

        # sum across all paths
        pathset_logsum_df = pathset_paths_df[[Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM, "logsum_component"]].groupby(
                                [Passenger.TRIP_LIST_COLUMN_PERSON_ID,Passenger.TRIP_LIST_COLUMN_TRIP_LIST_ID_NUM]).aggregate('sum').reset_index()
        pathset_logsum_df.rename(columns={"logsum_component":"logsum"}, inplace=True)
        pathset_paths_df = pandas.merge(left=pathset_paths_df,
                                        right=pathset_logsum_df,
                                        how="left")
        pathset_paths_df[Assignment.SIM_COL_PAX_PROBABILITY] = pathset_paths_df["logsum_component"]/pathset_paths_df["logsum"]

        if len(Assignment.TRACE_PERSON_IDS) > 0:
            FastTripsLogger.debug("calculate_cost: pathset_paths_df\n%s" % str(pathset_paths_df.loc[pathset_paths_df[Passenger.TRIP_LIST_COLUMN_PERSON_ID].isin(Assignment.TRACE_PERSON_IDS)]))

        # Note: the path finding costs won't match the costs here because missed transfers are already calculated here
        # It would be good to have some sanity checking that theyre aligned otherwise though to make sure we're
        # calculating costs consistently
        if False and (iteration % 2 == 1) and simulation_iteration == 0:
            # verify the cost matches what came from the C++ extension
            pathset_paths_df["cost_diff"    ] = pathset_paths_df[PathSet.PATH_KEY_COST] - pathset_paths_df[Assignment.SIM_COL_PAX_COST]
            pathset_paths_df["cost_pct_diff"] = pathset_paths_df["cost_diff"]/pathset_paths_df[PathSet.PATH_KEY_COST]
            cost_differs = pathset_paths_df.loc[abs(pathset_paths_df["cost_pct_diff"])>0.01]
            FastTripsLogger.debug("calculate_cost: cost_differs for %d rows\n%s" % (len(cost_differs), cost_differs.to_string()))
            if len(cost_differs) > 0:
                FastTripsLogger.warn("calculate_cost: cost_differs for %d rows\n%s" % (len(cost_differs), cost_differs.to_string()))

            pathset_paths_df["prob_diff"    ] = pathset_paths_df[PathSet.PATH_KEY_PROBABILITY] - pathset_paths_df[Assignment.SIM_COL_PAX_PROBABILITY]
            prob_differs = pathset_paths_df.loc[abs(pathset_paths_df["prob_diff"])>0.01]
            FastTripsLogger.debug("calculate_cost: prob_differs for %d rows\n%s" % (len(prob_differs), prob_differs.to_string()))
            if len(prob_differs) > 0:
                FastTripsLogger.warn("calculate_cost: prob_differs for %d rows\n%s" % (len(prob_differs), prob_differs.to_string()))

            pathset_paths_df.drop(["cost_diff","cost_pct_diff","prob_diff"], axis=1, inplace=True)


        return (pathset_paths_df, pathset_links_df)

