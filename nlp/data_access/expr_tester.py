#!/usr/bin/env python3
"""
This is a program for testing the ClarityNLP NLPQL expression evaluator.

It assumes that a run of the NLPQL file 'data_gen.nlpql' has already been
performed. You will need to know the job_id from that run to use this code.

Add your desired expression to the list in _run_tests, then evaluate it using
the data from your ClarityNLP run.
Use this command:

    python3 ./expr_tester.py --jobid <job_id> --mongohost <ip address>
                             --port <port number> --num <number> [--debug]


Help for the command line interface can be obtained via this command:

    python3 ./expr_tester.py --help

Extensive debugging info can be generated with the --debug option.

"""

import re
import os
import sys
import copy
import string
import optparse
import datetime
from pymongo import MongoClient
from collections import namedtuple
from bson import ObjectId

import expr_eval
import expr_result
from expr_result import HISTORY_FIELD

_VERSION_MAJOR = 0
_VERSION_MINOR = 3
_MODULE_NAME   = 'expr_tester.py'

_TRACE = False

_TEST_ID            = 'EXPR_TEST'
_TEST_NLPQL_FEATURE = 'EXPR_TEST'


_FILE_DATA_FIELDS = ['context', 'feat_expr_list']
FileData = namedtuple('FileData', _FILE_DATA_FIELDS)


###############################################################################
def _evaluate_expressions(expr_obj_list,
                          mongo_collection_obj,
                          job_id,
                          context_field,
                          is_final):
    """
    Nearly identical to
    nlp/luigi_tools/phenotype_helper.mongo_process_operations
    """

    phenotype_id    = _TEST_ID
    phenotype_owner = _TEST_ID
        
    assert 'subject' == context_field or 'report_id' == context_field

    all_output_docs = []
    is_final_save = is_final
    
    for expr_obj in expr_obj_list:

        # the 'is_final' flag only applies to the last subexpression
        if expr_obj != expr_obj_list[-1]:
            is_final = False
        else:
            is_final = is_final_save
        
        # evaluate the (sub)expression in expr_obj
        eval_result = expr_eval.evaluate_expression(expr_obj,
                                                    job_id,
                                                    context_field,
                                                    mongo_collection_obj)
            
        # query MongoDB to get result docs
        cursor = mongo_collection_obj.find({'_id': {'$in': eval_result.doc_ids}})

        # initialize for MongoDB result document generation
        phenotype_info = expr_result.PhenotypeInfo(
            job_id = job_id,
            phenotype_id = phenotype_id,
            owner = phenotype_owner,
            context_field = context_field,
            is_final = is_final
        )

        # generate result documents
        if expr_eval.EXPR_TYPE_MATH == eval_result.expr_type:

            output_docs = expr_result.to_math_result_docs(eval_result,
                                                          phenotype_info,
                                                          cursor)
        else:
            assert expr_eval.EXPR_TYPE_LOGIC == eval_result.expr_type

            # flatten the result set into a set of Mongo documents
            doc_map, oid_list_of_lists = expr_eval.flatten_logical_result(eval_result,
                                                                          mongo_collection_obj)
            
            output_docs = expr_result.to_logic_result_docs(eval_result,
                                                           phenotype_info,
                                                           doc_map,
                                                           oid_list_of_lists)
            
        if len(output_docs) > 0:
            mongo_collection_obj.insert_many(output_docs)
        else:
            print('mongo_process_operations ({0}): ' \
                  'no phenotype matches on "{1}".'.format(eval_result.expr_type,
                                                          eval_result.expr_text))

        # save the expr object and the results
        all_output_docs.append( (expr_obj, output_docs))

    return all_output_docs


###############################################################################
def _delete_prev_results(job_id, mongo_collection_obj):
    """
    Remove all docs generated by this module.
    """

    # delete all assigned results from a previous run of this code
    result = mongo_collection_obj.delete_many({"job_id":job_id,
                                               "nlpql_feature":_TEST_NLPQL_FEATURE})
    print('Removed {0} result docs from a previous run.'.
          format(result.deleted_count))

    # delete all temp results from a previous run of this code
    result = mongo_collection_obj.delete_many({"nlpql_feature":expr_eval.regex_temp_nlpql_feature})
    print('Removed {0} docs with temp NLPQL features from a previous run.'.
          format(result.deleted_count))
    

###############################################################################
def banner_print(msg):
    """
    Print the message centered in a border of stars.
    """

    MIN_WIDTH = 79

    n = len(msg)
    
    if n < MIN_WIDTH:
        ws = (MIN_WIDTH - 2 - n) // 2
    else:
        ws = 1

    ws_left = ws
    ws_right = ws

    # add extra space on right to balance if even
    if 0 == n % 2:
        ws_right = ws+1

    star_count = 1 + ws_left + n + ws_right + 1
        
    print('{0}'.format('*'*star_count))
    print('{0}{1}{2}'.format('*', ' '*(star_count-2), '*'))
    print('{0}{1}{2}{3}{4}'.format('*', ' '*ws_left, msg, ' '*ws_right, '*'))
    print('{0}{1}{2}'.format('*', ' '*(star_count-2), '*'))
    print('{0}'.format('*'*star_count))
    
    
###############################################################################
def _run_tests(job_id,
               final_nlpql_feature,
               command_line_expression,
               context_var,
               mongo_collection_obj,
               #mongohost,
               #port,
               num,
               is_final,
               debug=False):
    """
    Include all NLPQL names from data_gen.nlpql in the following list.
    """

    global _TRACE
    
    NAME_LIST = [
        'hasRigors', 'hasDyspnea', 'hasNausea', 'hasVomiting', 'hasShock',
        'hasTachycardia', 'hasLesion', 'Temperature', 'Lesion',
        'hasFever', 'hasSepsisSymptoms', 'hasTempAndSepsisSymptoms',
        'hasSepsis', 'hasLesionAndSepsisSymptoms', 'hasLesionAndTemp',
        'hasLesionTempAndSepsisSymptoms'
    ]

    EXPRESSIONS = [

        # # pure math expressions
        # 'Temperature.value >= 100.4',
        # 'Temperature.value >= 1.0004e2',
        # '100.4 <= Temperature.value',
        # '(Temperature.value >= 100.4)',
        # 'Temperature.value == 100.4', # 28 results
        # 'Temperature.value + 3 ^ 2 < 109',      # temp < 100, 659 results
        # 'Temperature.value ^ 3 + 2 < 941194',   # temp < 98, 218 results
        # 'Temperature.value % 3 ^ 2 == 2',       # temp == 101, 169 results
        # 'Temperature.value * 4 ^ 2 >= 1616',    # temp >= 101, 1128 results
        # 'Temperature.value / 98.6 ^ 2 < 0.01',  # temp < 97.2196, 114 results
        # '(Temperature.value / 98.6)^2 < 1.02',  # temp < 99.581, 590 results
        # '0 == Temperature.value % 20',          # temp == 100, 145 results
        # '(Lesion.dimension_X <= 5) OR (Lesion.dimension_X >= 45)',
        # 'Lesion.dimension_X > 15 AND Lesion.dimension_X < 30',             # 1174 results
        # '((Lesion.dimension_X) > (15)) AND (((Lesion.dimension_X) < (30)))', # 1174 results

        # # math involving multiple NLPQL features
        # 'Lesion.dimension_X > 15 AND Lesion.dimension_X < 30 OR (Temperature.value >= 100.4)',
        # '(Lesion.dimension_X > 15 AND Lesion.dimension_X < 30) OR (Temperature.value >= 100.4)',
        # 'Lesion.dimension_X > 15 AND Lesion.dimension_X < 30 AND Temperature.value > 100.4',
        # # #### not legal, since each math expression must produce a Boolean result:
        # # # '(Temp.value/98.6) * (HR.value/60.0) * (BP.systolic/110) < 1.1',

        # # pure logic expressions
        # 'hasTachycardia',
        # 'hasSepsis',
        # 'hasTempAndSepsisSymptoms',
        # 'Temperature AND hasSepsisSymptoms',
        # 'hasTachycardia AND hasShock', # subjects 14894, 20417
        # 'hasTachycardia OR hasShock',
        # 'hasTachycardia AND hasDyspnea', # subjects 22059, 24996, 
        # '((hasShock) AND (hasDyspnea))',
        # '((hasTachycardia) AND (hasRigors OR hasDyspnea OR hasNausea))', # 313
        # '((hasTachycardia)AND(hasRigorsORhasDyspneaORhasNausea))',
        # 'hasRigors AND hasTachycardia AND hasDyspnea', # 13732, 16182, 24799, 5701
        # 'hasRigors OR hasTachycardia AND hasDyspnea', # 2662
        # 'hasRigors AND hasDyspnea AND hasTachycardia', # 13732, 16182, 24799, 7480, 5701,
        # '(hasRigors OR hasDyspnea) AND hasTachycardia', #286
        # 'hasRigors AND (hasTachycardia AND hasNausea)',
        # '(hasShock OR hasDyspnea) AND (hasTachycardia OR hasNausea)',
        # 'hasFever AND (hasDyspnea OR hasTachycardia)',

        # mixed math and logic 
        'hasNausea AND Temperature.value >= 100.4',
        # 'Lesion.dimension < 10 OR hasRigors',
        # '(hasRigors OR hasTachycardia OR hasNausea OR hasVomiting or hasShock) AND (Temperature.value >= 100.4)',
        # 'Lesion.dimension_X > 10 AND Lesion.dimension_X < 30 AND (hasRigors OR hasTachycardia or hasDyspnea)',
        # 'Lesion.dimension_X > 10 OR Lesion.dimension_X < 30 OR hasRigors OR hasTachycardia or hasDyspnea',
        # '((Temperature.value >= 100.4) AND (hasRigors AND hasTachycardia AND hasNausea))',
        # 'Temperature.value >= 100.4 OR hasRigors OR hasTachycardia OR hasDyspnea OR hasNausea',
        # 'hasRigors AND hasTachycardia AND hasDyspnea AND hasNausea AND Temperature.value >= 100.4',
        # 'hasRigors OR (hasTachycardia AND hasDyspnea) AND Temperature.value >= 100.4',
        # 'hasRigors OR hasTachycardia OR hasDyspnea OR hasNausea AND Temperature.value >= 100.4',
        ### 'Lesion.dimension_X < 10 OR hasRigors AND Lesion.dimension_X > 30',
        # 'Lesion.dimension_X > 12 AND Lesion.dimension_X > 20 AND Lesion.dimension_X > 35 OR hasNausea and hasDyspnea',
        # 'M.x > 12 AND M.x > 15 OR M.x < 25 AND M.x < 32 OR hasNausea and hasDyspnea',
        # 'M.x > 12 AND M.x > 15 OR M.x < 25 AND M.x < 32 AND hasNausea OR hasDyspnea',

        # problem (dimension_X and dimension_Y)
        # 'Temperature.value >= 100.4 OR hasRigors AND hasDyspnea OR Lesion.dimension_X > 10 OR Lesion.dimension_Y < 30',

        # # error
        #'This is junk and should cause a parser exception',
    ]

    # must either be a patient or document context
    context_var = context_var.lower()
    assert 'patient' == context_var or 'document' == context_var

    if 'patient' == context_var:
        context_field = 'subject'
    else:
        context_field = 'report_id'

    # cleanup so that database only contains data generated by data_gen.nlpql
    # not from previous runs of this test code
    _delete_prev_results(job_id, mongo_collection_obj)

    if debug:
        expr_eval.enable_debug()
        _TRACE = True
    
    counter = 1
    for e in EXPRESSIONS:

        # override with expression from the command line, if any
        if command_line_expression is not None:
            e = command_line_expression

        print('[{0:3}]: "{1}"'.format(counter, e))

        parse_result = expr_eval.parse_expression(e)#, NAME_LIST)
        if 0 == len(parse_result):
            print('\n*** parse_expression failed ***\n')
            break
        
        # generate a list of ExpressionObject primitives
        expression_object_list = expr_eval.generate_expressions(final_nlpql_feature,
                                                                parse_result)
        if 0 == len(expression_object_list):
            print('\n*** generate_expressions failed ***\n')
            break

        # evaluate the ExpressionObjects in the list
        results = _evaluate_expressions(expression_object_list,
                                        mongo_collection_obj,
                                        job_id,
                                        context_field,
                                        is_final)

        banner_print(e)
        for expr_obj, output_docs in results:
            print()
            print('Subexpression text: {0}'.format(expr_obj.expr_text))
            print('Subexpression type: {0}'.format(expr_obj.expr_type))
            print('      Result count: {0}'.format(len(output_docs)))
            print('     NLPQL feature: {0}'.format(expr_obj.nlpql_feature))
            print('Results: ')

            n = len(output_docs)
            if 0 == n:
                print('\tNone.')
                continue

            if expr_eval.EXPR_TYPE_MATH == expr_obj.expr_type:
                for k in range(n):
                    if k < num or k > n-num:
                        doc = output_docs[k]
                        #print(doc)
                        print('\t[{0:6}]: {1} {2} {3} {4} {5}'.
                              format(k, doc['_id'], doc['nlpql_feature'],
                                     doc['value'], doc['subject'],
                                     doc['report_id']))
                    elif k == num:
                        print('\t...')

            else:
                for k in range(n):
                    if k < num or k > n-num:
                        doc = output_docs[k]
                        print('[{0:6}]: Document {1}, NLPQL feature {2}:'.
                              format(k, str(doc['_id']),
                                     expr_obj.nlpql_feature))

                        if is_final:
                            print(doc)
                        else:
                            history = doc[HISTORY_FIELD]
                            for tup in history:
                                if isinstance(tup.data, float):

                                # format data depending on whether float or string
                                    data_string = '{0:<10}'.format(tup.data)
                                else:
                                    data_string = '{0}'.format(tup.data)

                                if 'subject' == context_field:
                                    context_str = 'subject: {0}'.format(tup.subject)
                                else:
                                    context_str = 'report_id: {0}'.format(tup.report_id)

                                print('\t_id: {0}, operation: {1:20} '  \
                                      'nlpql_feature: {2:40} {3} ' \
                                      'data: {4} '.
                                      format(tup.oid, tup.pipeline_type,
                                             tup.nlpql_feature, context_str,
                                             data_string))
                    elif k == num:
                        print('\t...')
                
        counter += 1
        print()

        # exit if user provided an expression on the command line
        if command_line_expression is not None:
            break

    return True


###############################################################################
def _parse_file(filepath):
    """
    Read the NLPQL file and extract the context, nlpql_features, and 
    associated expressions. Returns a FileData namedtuple.
    """

    str_context_statement = r'context\s(?P<context>[^;]+);'
    regex_context_statement = re.compile(str_context_statement, re.IGNORECASE)
    
    str_define_statement = r'\bdefine\s(?P<feature>[^:]+):\swhere\s(?P<expr>[^;]+);'
    regex_define_statement = re.compile(str_define_statement, re.IGNORECASE)
    
    with open(filepath, 'rt') as infile:
        text = infile.read()

    # replace newlines with spaces for regex simplicity
    text = re.sub(r'\n', ' ', text)

    # replace repeated spaces with a single space
    text = re.sub(r'\s+', ' ', text)

    tuple_list = []

    match = regex_context_statement.search(text)
    if match:
        context = match.group('context').strip()
    
    iterator = regex_define_statement.finditer(text)
    for match in iterator:
        nlpql_feature = match.group('feature').strip()
        expression    = match.group('expr').strip()
        tuple_list.append( (nlpql_feature, expression) )

    return FileData(
        context = context,
        feat_expr_list = tuple_list
    )
        

###############################################################################
def _get_version():
    return '{0} {1}.{2}'.format(_MODULE_NAME, _VERSION_MAJOR, _VERSION_MINOR)


###############################################################################
def _show_help():
    print(_get_version())
    print("""
    USAGE: python3 ./{0} --jobid <integer> [-cdhvmpnef]

    OPTIONS:

        -j, --jobid    <integer>   job_id of data in MongoDB
        -c, --context  <string>    either 'patient' or 'document'
                                   (default is patient)
        -m, --mongohost            IP address of remote MongoDB host
                                   (default is localhost)
        -p, --port                 port number for remote MongoDB host
                                   (default is 27017)

        -n, --num                  Number of results to display at start and
                                   end of results array (the number of results
                                   displayed is 2 * n). Default is n == 16.

        -f, --file                 NLPQL file to process. Must contain only 
                                   define statements. If this option is present
                                   the -e option cannot be used.

        -e, --expr                 NLPQL expression to evaluate.
                                   (default is to use a test expression from this file)
                                   If this option is present the -f option
                                   cannot be used.
    FLAGS:

        -h, --help           Print this information and exit.
        -d, --debug          Enable debug output.
        -v, --version        Print version information and exit.
        -i, --isfinal        Generate NLPQL 'final' result. Default is to
                             generate an 'intermediate' result.

    """.format(_MODULE_NAME))


###############################################################################
if __name__ == '__main__':

    optparser = optparse.OptionParser(add_help_option=False)
    optparser.add_option('-c', '--context', action='store', dest='context')
    optparser.add_option('-j', '--jobid', action='store', dest='job_id')
    optparser.add_option('-d', '--debug', action='store_true',
                         dest='debug', default=False)
    optparser.add_option('-v', '--version',
                         action='store_true', dest='get_version')
    optparser.add_option('-h', '--help',
                         action='store_true', dest='show_help', default=False)
    optparser.add_option('-i', '--isfinal',
                         action='store_true', dest='isfinal', default=False)
    optparser.add_option('-m', '--mongohost', action='store', dest='mongohost')
    optparser.add_option('-p', '--port', action='store', dest='port')
    optparser.add_option('-n', '--num', action='store', dest='num')
    optparser.add_option('-e', '--expr', action='store', dest='expr')
    optparser.add_option('-f', '--file', action='store', dest='filepath')

    opts, other = optparser.parse_args(sys.argv)

    if opts.show_help or 1 == len(sys.argv):
        _show_help()
        sys.exit(0)

    if opts.get_version:
        print(_get_version())
        sys.exit(0)

    debug = False
    if opts.debug:
        debug = True

    if opts.job_id is None:
        print('The job_id (-j command line option) must be provided.')
        sys.exit(-1)
    job_id = int(opts.job_id)

    mongohost = 'localhost'
    if opts.mongohost is not None:
        mongohost = opts.mongohost

    port = 27017
    if opts.port is not None:
        port = int(opts.port)

    is_final = opts.isfinal

    context = 'patient'
    if opts.context is not None:
        context = opts.context

    num = 16
    if opts.num is not None:
        num = int(opts.num)

    expr = None
    if opts.expr is not None:
        if opts.filepath is not None:
            print('Options -e and -f are mutually exclusive.')
            sys.exit(-1)
        else:
            expr = opts.expr

    filepath = None
    if opts.filepath is not None:
        if opts.expr is not None:
            print('Options -e and -f are mutually exclusive.')
            sys.exit(-1)
        else:
            filepath = opts.filepath
            if not os.path.exists(filepath):
                print('File not found: "{0}"'.format(filepath))
                sys.exit(-1)
            file_data = _parse_file(filepath)

    # connect to ClarityNLP mongo collection nlp.phenotype_results
    mongo_client_obj = MongoClient(mongohost, port)
    mongo_db_obj = mongo_client_obj['nlp']
    mongo_collection_obj = mongo_db_obj['phenotype_results']

    if filepath is None:
        # command-line expression uses the test feature
        final_nlpql_feature = _TEST_NLPQL_FEATURE
    
        _run_tests(job_id,
                   final_nlpql_feature,
                   expr,
                   context,
                   mongo_collection_obj,
                   num,
                   is_final,
                   debug)
    else:
        context = file_data.context
        for nlpql_feature, expression in file_data.feat_expr_list:
            _run_tests(job_id,
                       nlpql_feature,
                       expression,
                       context,
                       mongo_collection_obj,
                       num,
                       is_final,
                       debug)
            
