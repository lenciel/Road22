"""
This package is a multi-threaded generic web crawler.
"""

import os
from convert import *
from datetime import datetime
from optparse import OptionParser
import sys
import urlparse
import urllib2
import robotparser
from threading import *
import re
import logging
import socket
from time import sleep
from random import *
import lxml.html as H
from lxml.html.clean import Cleaner
from lxml.builder import E
from lxml.builder import ElementMaker
from lxml.etree import SubElement,Element,CDATA,tostring
from yculanalyzer import *
# configuration for the logger
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    filename='all2w.log',
                    filemode='w')

class StupidAnalyzer(Thread):
    """
    This is an basic analyzer implementation. You should write your own
    subclass contain your logic of analyzing the gathered data and
    choosing which links to crawl later.
    """

    def __init__(self, linksToFetchAndCond, siteQueueAndCond, db, userName):
        """
        Creates a new analyzer. There can be as many analyzers as you like,
        depending on the type of processing of data you wish to do.
        @param linksToFetchAndCond: A tuple of the form (map, Condition) where the map
        is the map of domains to links to be fetched (which is updated by the analyzer)
        and the condition is a L{threading.Condition} object which is used to synchronize
        access to the list.
        @param siteQueueAndCond: A tuple (siteQueue, siteCond). The siteQueue is a queue
        unto which new sites (with their links) are inserted. The siteCond is a Condition
        object used to lock the queue and signal the analyzer to wake up.
        @param db: a tuple of the form (siteDb, siteDbLock) where the linkDb
        is the global link database, siteDb is the global site database and the lock
        is used to synchronize access to both of these databases.
        """
        Thread.__init__(self)
        self.linksToFetchAndCond = linksToFetchAndCond
        self.db = db
        self.siteQueueAndCond = siteQueueAndCond
        self.__usrName = userName
        # stop condition variable
        self.__stopCondition = False

        # a lock to lock the stop condition
        self.__scl = Lock()

        # initialize the db
        if not self.db[0].has_key('crawled'):
            self.db[0]['crawled'] = {}

        if not self.db[0].has_key('sites'):
            self.db[0]['pages'] = {}

        self.__sitesProcessed = 0

    def setStopCondition(self, val):
        """
        Sets the stop condition to the specified value. Should be True to stop
        the analyzer thread.
        """
        self.__scl.acquire()
        self.__stopCondition = val
        self.__scl.release()

    def getNumSitesProcessed(self):
        """
        Returns the number of sites this analyzer has processed
        """
        return self.__sitesProcessed

    def run(self):
        """
        Performs the main function of the analyzer. In this case,
        just adds all the hyperlinks to the toFetch queue.
        """
        logging.info("Starting the analyzer thread")
        # separate the tuples for convinience
        lfs, lfsCond = self.linksToFetchAndCond
        siteDb, dbLock = self.db
        siteQueue, siteQueueCond = self.siteQueueAndCond

        # repeat while the stop condition hasn't been set
        self.__scl.acquire()
        siteQueueCond.acquire()
        while (not self.__stopCondition) or (len(siteQueue) != 0):
            # check if there's anything in the queue
            while (len(siteQueue) == 0) and (not self.__stopCondition):
                self.__scl.release()
                siteQueueCond.wait()
                self.__scl.acquire()

            if (self.__stopCondition) and (len(siteQueue) == 0):
                break

            # release the stop condition lock for now
            self.__scl.release()

            # get a site to process
            #logging.debug("siteQueue is [%s]" % (str([s.stringUrl for s in siteQueue])))
            siteToProcess = siteQueue.pop()
            self.__sitesProcessed += 1

            # release the lock on the site queue
            siteQueueCond.release()

            # process the new site
            dbLock.acquire()
            self.analyzeSite(siteDb, siteToProcess, self.__usrName)
            dbLock.release()

            # add links to the links to fetch queue
            lfsCond.acquire()
            self.addSiteToFetchQueue(lfs)
            lfsCond.notify()
            lfsCond.release()

            # reacquire the lock before the while condition check
            self.__scl.acquire()

            # reacquire the site queue lock before the while condition check
            siteQueueCond.acquire()

        self.__scl.release()
        # release the lock on the site queue
        siteQueueCond.release()

    def analyzeSite(self, db, site):
        """
        Processes the site and adds it to the db.
        Any real analyzer should override this method with it's
        own logic.
        """
        # check if the site was already crawled
        #if db['crawled'].has_key(site.stringUrl):
        #    self.__newLinksToCrawl = []
        #    return

        # add the site
        db['sites'][site.stringUrl] = site
        db['crawled'][site.stringUrl] = True

        # decide which links to crawl (in this case all regular links)
        self.__newLinksToCrawl = [link for link in site.rawContent.iterlinks() if (not db['crawled'].has_key(link))]
        #logging.debug("Site: [%s], The new ltc of the analyzer is: [%s]" % (site.stringUrl, str(self.__newLinksToCrawl)))

        tempList = []
        for l in self.__newLinksToCrawl:
            db['crawled'][l] = True
            if not l in tempList:
                tempList += [l]
        self.__newLinksToCrawl = tempList


    def addSiteToFetchQueue(self, lfs):
        """
        Adds links to the fetch queue. A real analyzer should override
        this method.
        """
        logging.debug("Adding to lfs")
        lniks = self.__newLinksToCrawl
        for l in lniks:
            if lfs.has_key(l):
                lfs[l] += domMap[l]
            else:
                lfs[l] = domMap[l]

    def selectNextUrl(self):
        """
        Chooses the next url to crawl to. This implementation
        will select a random domain and then crawl to the first link
        in that domain's queue.
        """
        toFetchQueue = self.linksToFetchAndCond[0]
        dom = toFetchQueue.keys()
        selectedDom = dom[randint(0,len(dom) - 1)]
        curUrl = toFetchQueue[selectedDom].pop()
        if len(toFetchQueue[selectedDom]) == 0:
            toFetchQueue.pop(selectedDom)
        return curUrl

    def report(self):
        """
        A real analyzer should override this method. Outputs the results
        of the analysis so far.
        """
        None



class Controller(Thread):
    """
    This class is responsible for controlling the fetchers and distributing
    the work load.
    """
    def __init__(self, linksToFetchAndCond, siteQueueAndCond,analyzer,options):
        """
        Creates a new controller. Typically you will need only one.
        options.fetcherThreads, options.maxUrlsToCrawl, options.timeOut, options.delay
        @param linksToFetchAndCond: A tuple of the form (Dict, Condition) where the dict
        is the map of domains to links to be fetched (which is updated by the analyzer)
        and the condition is a L{threading.Condition} object which is used to synchronize
        access to the list.
        @param siteQueueAndCond: a tuple of the form (list, cond) where list is the site queue
        into which fetchers insert new sites that have been fetched. cond is a Condition
        object which is used to lock the queue.
        @param analyzer: The analyzer which we are using for analyzing crawled data.
        @param numFetchers: The number of active threads used for crawling.
        @param maxFetches: Maximum number of pages to crawl.
        @param socketTimeOut: The timeout to use for opening urls.
        WARNING: the timeout is set using socket.setdefaulttimeout(..)
        which affects ALL the sockets in the multiverse.
        @param delay: The delay in seconds between assignments of urls to fetchers.
        """
        Thread.__init__(self)

        self.__linksToFetchAndCond = linksToFetchAndCond
        self.__db = siteQueueAndCond
        self.__maxFetches = options.maxPagesToCrawl

        # set the timeout
        socket.setdefaulttimeout(options.timeOut)

        # the number of sites fetched so far
        self.__numFetches = 0

        # initialize the fetcher pool
        self.__fetchers = []

        # create the requested number of fetchers
        siteQueue, siteQueueCond = siteQueueAndCond
        for i in range(options.fetcherThreads):
            # create the condition for the fetcher
            c = Condition()

            # create the stop condition lock
            scl = Lock()

            # create the fetcher
            f = Fetcher(siteQueue, siteQueueCond, c, scl)

            # store the information about the fetcher
            self.__fetchers += [(f, c, scl)]

        self.__verificationMap = {}
        self.__delay = options.delay

        self.__analyzer = analyzer

    def getFetcherThreadUtilization(self):
        """
        Returns a list of number of urls each fetcher thread handler.
        """
        return [ftuple[0].getUrlsCounter() for ftuple in self.__fetchers]

    def getNumFetchersUsed(self):
        """
        Returns the number of fetchers that handled at least one url.
        """
        u = [ftuple[0].getUrlsCounter() for ftuple in self.__fetchers]
        counter = 0
        for x in u:
            if x > 0:
                counter += 1
        return counter

    def run(self):
        """
        Runs this controller thread. The controller will use it's
        fetchers to fill the links and sites databases.
        """
        logging.info("Starting the controller thread")

        # start all the fetcher threads
        for ftuple in self.__fetchers:
            ftuple[0].start()

        # let's, for comfort, separate the linksToFetchAndCond
        toFetchQueue, toFetchCond = self.__linksToFetchAndCond
        while (self.__analyzer.getStopSign()==1) and (self.__numFetches < self.__maxFetches):
            sleep(self.__delay)
            # Check if we have something to fetch
            logging.debug('getting cond')
            toFetchCond.acquire()
            logging.debug('got cond')
            while (len(toFetchQueue) == 0):
                logging.debug('waiting')
                toFetchCond.wait()

            logging.debug('done waiting')
            # pop a url to fetch
            curUrl = self.__analyzer.selectNextUrl()
            #print str(toFetchQueue)
            if self.__verificationMap.has_key(curUrl):
                #raise Exception, ("Duplicate URL [%s]" % curUrl)
                break
            else:
                self.__verificationMap[curUrl] = True
            #for key in self.__verificationMap: print 'key=%s, value=%s' % (key, self.__verificationMap[key])

            logging.debug("Controller acquired URL: [%s]" % curUrl)

            # release the lock
            toFetchCond.release()
            # increment the counter of fetched urls (we don't care if it succeeded)
            self.__numFetches += 1

            logging.info("Processed %d out of %d pages" % (self.__numFetches, self.__maxFetches))

            # find some fetcher to take the url
            foundFreeFetcher = False
            while (not foundFreeFetcher):
                for ftuple in self.__fetchers:
                    # if we can lock the condition then the fetcer might be free
                    if (ftuple[1].acquire(False)):
                        # if the fetcher is indeed free assign it a new url
                        if ftuple[0].isFree():
                            logging.debug("Controller found free fetcher")
                            # assign the new url to the fetcher
                            ftuple[0].setUrl(curUrl)
                            ftuple[1].notify()
                            ftuple[1].release()
                            foundFreeFetcher = True
                            break
                        else:
                            # if not, nudge it
                            ftuple[1].notify()
                            ftuple[1].release()

        # stop the fetchers
        self.__stopFetchers()
        logging.debug("Stopping controller, %d fetcher threads were useful." % self.getNumFetchersUsed())

#    def selectNextUrl(self, toFetchQueue):
#        dom = toFetchQueue.keys()
#        selectedDom = dom[randint(0,len(dom) - 1)]
#        curUrl = toFetchQueue[selectedDom].pop()
#        if len(toFetchQueue[selectedDom]) == 0:
#            toFetchQueue.pop(selectedDom)
#        return curUrl

    def __stopFetchers(self):
        """
        Stops all the fetchers
        """
        logging.debug("Stopping fetchers")
        for ftuple in self.__fetchers:
            # set the stop condition
            ftuple[2].acquire()
            ftuple[0].setStopCondition(True)
            ftuple[2].release()

            # notify the fetcher
            ftuple[1].acquire()
            ftuple[1].notify()
            ftuple[1].release()

            # wait for the fetcher to terminate
            ftuple[0].join()


class Fetcher(Thread):
    """
    This class is responsible for fetching url contents, processing them
    with UgrahExtractor and updating the site and link database.
    """

    def __init__(self, siteQueue, siteQueueCond, fetcherCondition, stopConditionLock):
        """
        Creates a new fetcher thread (not started) with the following
        @param siteQueue: the site queue from which the analyzer takes sites
        to analyze.
        @param siteQueueCond: A Condition object used to lock the siteQueue.
        @param fetcherCondition: a threading.Condition object which is used for
        communication between the fetcher and the controller: whenever
        a fetcher finishes working on it's assignment it calls
        fetcherCondition.wait() and waits until the controller assigns
        a new url for it to fetch.
        @param stopConditionLock: a threading.Lock object which is used to lock
        the internal stop condition variable. A thread that wishes
        to change this variable should lock it first.
        """
        Thread.__init__(self)
        self.siteQueue = siteQueue
        self.condition = fetcherCondition
        self.siteQueueCond = siteQueueCond
        self.stopConditionLock = stopConditionLock

        # the stop condition, the loop will run while it's false
        self.stopCondition = False

        # the url of the site we're currently supposed to process
        self.currentStringUrl = None

        # the url handler used for processing
        self.urlHandler = UrlHandler()

        # the extractor we're going to use to process the page
        self.extractor = Extractor()

        # a statistical used for debugging and ... statistics
        self.handledUrlsCounter = 0

    def setStopCondition(self, val):
        """
        Can receive either True or False. Set to Ture when the fetcher
        should stop working. WARNING: It's *necessary* to acquire the lock
        which was passed to the constructor as stopConditionLock before
        calling this method.
        """
        self.stopCondition = val

    def setUrl(self, stringUrl):
        """
        Sets the url that the fetcher should work on. It's *necessary*
        to acquire the condition instance which was passed to the constructor
        as fetcherCondition before calling this method and call notify afterwards
        """
        self.currentStringUrl = stringUrl

    def getUrlsCounter(self):
        """
        Returns the number of URLs this fetcher has handled.
        Should be called only AFTER the thread is dead.
        """
        return self.handledUrlsCounter

    def isFree(self):
        """
        Returns True if the fetcher hasn't been assigned a URL yet.
        """
        return (self.currentStringUrl == None)

    def run(self):
        """
        Performs the main function of the fetcher which is to fetch
        the contents of the url specified by setCurrentStringUrl.
        This method loops until the stop condition is set.
        """
        logging.info("Starting the fetcher thread")
        # lock our condition (this a is standard pattern)
        self.condition.acquire()

        # lock the stop condition variable
        self.stopConditionLock.acquire()
        while (not self.stopCondition) or (self.currentStringUrl):
            # wait until we get a new url to fetch
            while (not self.currentStringUrl) and (not self.stopCondition):
                # release the stop condition lock
                self.stopConditionLock.release()
                self.condition.wait()
                self.stopConditionLock.acquire()

            # we have to check the stop condition since it could
            # have changed while we were waiting
            if self.stopCondition and (not self.currentStringUrl):
                # we do not release the lock here because it is
                # release immidiately after exiting the loop
                break
            else:
                self.stopConditionLock.release()

            # increase the url counter
            self.handledUrlsCounter += 1
            #logging.debug("URL acquired by fetcher: [%s]", self.currentStringUrl)

            try:
                # step 1: fetch the url
                #######################

                # process the site
                self.__processSite()

                # retrieve the data
                parsedContent = self.extractor.getParsedContent()
                rawContent = self.extractor.getRawContent()

                # step 2: file the retrieved data
                #################################
                s = Site(self.currentStringUrl, parsedContent, rawContent)
                self.__fileData(s)
                logging.info("URL processing succeeded: [%s]" % self.currentStringUrl)
            except Exception, e:
                logging.info("URL processing failed: [%s], error: %s" % (self.currentStringUrl, e))

            # nullify the current url
            self.currentStringUrl = None

            # lock the stop condition variable for the next while check
            self.stopConditionLock.acquire()

        # release the condition
        self.condition.release()
        # release the stop condition lock
        self.stopConditionLock.release()


    def __fileData(self, s):
        """
        Stores the given site and links in the databases
        """
        # lock the db
        self.siteQueueCond.acquire()

        # store the new site information
        self.siteQueue.insert(0, s)

        # wake an analyzer
        self.siteQueueCond.notify()

        # unlock the db
        self.siteQueueCond.release()

    def __processSite(self):
        """
        Fetches the url contents and creates a parsed structure
        """
        self.urlHandler.processUrl(self.currentStringUrl)
        content = self.urlHandler.getSite()
        self.extractor.setSite(self.currentStringUrl, content)

class Site:
    """
    A class for representing the information that is collected for
    a specific site.
    """

    def __init__(self, stringUrl, parsedContent, rawContent):
        """
        Creates a new site.
        @param stringUrl: the url of the site
        @param parsedContent: BeautifulSoup instance which contains the parsed content
        of the page.
        @param rawContent: The raw content of the page as a string.
        """
        self.stringUrl = stringUrl
        self.parsedContent = parsedContent
        self.rawContent = rawContent
        self.matches = []

class Extractor:

    def __init__(self):
        """
        Creates a new link extractor. Should be followed by a call to setSite
        """

        # The string representation of the url
        self.stringUrl = None

        # the domain of the url
        self.domain = None

        # The BeautifulSoup instance which contains the html
        # tree for this site
        self.parsedContent = None
        self.rawContent = None

    def setSite(self, stringUrl, content):
        """
        Sets the current site url and content for the extractor.
        @param stringUrl: The url of the site being analyzed.
        @param content: The html content of the site.
        """
        # remove trailing / characters from the base ur
        self.stringUrl = stringUrl #.rstrip('/ ')
        preDomain = urlparse.urlparse(self.stringUrl)
        self.domain = urlparse.urlunparse((preDomain[0], preDomain[1],'', '', '', ''))
        #self.path = preDomain[2]
        #self.parentPath = preDomain[2].split('/')
        #self.parentPath = '/'.join(self.parentPath[0:-1])

        # parse the content
        fullContent = content.read()
        #self.parsedContent = BeautifulSoup(fullContent)
        doc = H.document_fromstring(fullContent.decode('utf-8'))
        doc.make_links_absolute(extractBaseUrl(stringUrl))
        self.parsedContent = doc
        self.rawContent = H.tostring(doc, pretty_print=True, include_meta_content_type=True,encoding=unicode,method='html')
        #cleaner = Cleaner(style=True, links=True, add_nofollow=True, page_structure=False, safe_attrs_only=False)
        #self.parsedContent = H.tostring(doc, pretty_print=True, include_meta_content_type=True,encoding=unicode,method='html')
        #self.rawContent = doc
        #logging.debug("Extractor url set. Soup created for: [%s]" % stringUrl)

    def getParsedContent(self):
        """
        Returns the BeautifulSoup datastructure of the HTML of the
        site that was set using setSite .
        """
        return self.parsedContent

    def getRawContent(self):
        return self.rawContent

class UrlHandler:
    """
    A class responsible for parsing a url and retrieving it's contents.
    """

    def __init__(self):
        """
        A constructor for the url handler. Called after setCurrentUrl and getSite.
        """
        # initialize the robot parser
        self.robotParser = robotparser.RobotFileParser()
        self.currentSite = None

    def processUrl(self, stringUrl):
        """
        Sets the url that the parser is working on.
        Raises an exception if we can't open it.
        """
        self.robotParser = robotparser.RobotFileParser()
        # check access rights, if not ok raise exception
        if not self.__canVisitSite(stringUrl):
            logging.info("access to [%s] was denied by robots.txt" % stringUrl)
            raise Exception, "robots.txt doesn't allow access to %s" % stringUrl

        # create the HTTP request
        req = self.__createRequest(stringUrl)
        # open the url and set our site to the opened url
        site = urllib2.urlopen(req)

        if (not (site.headers.type == 'text/html')) and (not (site.headers.type == 'application/x-javascript')):
            logging.info('Url contained mime type which is not text/html: [%s]' % stringUrl)
            raise Exception, "Not text/html mime type"

        logging.info("successfully opened %s" % stringUrl)
        self.currentSite = site

    def getSite(self):
        """
        Returns the url object which was opened by setCurrentUrl.
        The returned object acts just like a file object.
        """
        return self.currentSite

    def __createRequest(self, stringUrl):
        req = urllib2.Request(stringUrl)
        req.add_header('User-agent', 'Ugrah/0.1')
        #req.add_header('Accept', 'text/html')
        return req

    def __canVisitSite(self, stringUrl):
        """
        Checks whether we are allowed by robots.txt to visit some page.
        Returns true if we can, false otherwise.
        """
        # extract the robots.txt url
        parsedUrl = urlparse.urlparse(stringUrl)
        robotsUrl = urlparse.urlunparse((parsedUrl[0], parsedUrl[1], "robots.txt",
                                         parsedUrl[3], parsedUrl[4], parsedUrl[5]))
        #logging.debug("Robots for [%s] is [%s]" % (stringUrl, robotsUrl))

        # parse robots.txt
        self.robotParser.set_url(robotsUrl)
        self.robotParser.read()

        # check permission to access page
        return self.robotParser.can_fetch("Ugrah/0.1", stringUrl)
class Exporter:


    CONTENT_NAMESPACE = 'http://purl.org/rss/1.0/modules/content/'
    WFW_NAMESPACE = 'http://wellformedweb.org/CommentAPI/'
    DC_NAMESPACE = 'http://purl.org/dc/elements/1.1/'
    WP_NAMESPACE = 'http://wordpress.org/export/1.0/'
    CONTENT = "{%s}" % CONTENT_NAMESPACE
    WFW = "{%s}" % WFW_NAMESPACE
    DC = "{%s}" % DC_NAMESPACE
    WP = "{%s}" % WP_NAMESPACE
    NSMAP = {'wfw' : WFW_NAMESPACE,
             'content': CONTENT_NAMESPACE,
             'dc' : DC_NAMESPACE,
             'wp' : WP_NAMESPACE}
    """
    exporter class to export a wrx file.
    """
    def __init__(self, options):
        self.__title = options.Title
        self.__desc = options.Description
        self.__url = options.url
        self.__output_file = options.output
        self.__rss = Element("rss", version="2.0", nsmap=self.NSMAP)
        self.__channel = SubElement(self.__rss, "channel")
        #self.__channel = self._create_sub_with_text(self.__rss, "channel","")
        self.__db = {}
        self.__postId = 0

    def _create_sub_with_text(parent,child,text):
        if text != "" :
            childEl = SubElement(parent,child)
            childEl.text = unicode(text)
        else :
            childEl = SubElement(parent,child)
        return childEl

    def _create_category(self, nicename, name=""):
        """Creates a Category."""
        if name != "" and name:
            category = SubElement(self.__channel,self.WP+"category")
            category_nicename=SubElement(category,self.WP+'category_nicename')
            category_nicename.text = unicode(nicename)
            category_parent=SubElement(category,self.WP+'category_parent')
            catname=SubElement(category,self.WP+'cat_name')
            catname.text=unicode(name)
            catname.text = CDATA(catname.text)

    def _create_tags(self,tagName):
        """Creates a Tag."""
        newtag = SubElement( self.__channel,self.WP+"category")
        tag_slug=SubElement(newtag,self.WP+'tag_slug')
        tag_slug.text = self._to_pin_yin(unicode(tagName))
        element = SubElement(newtag,self.WP+'tag_name')
        element.text = unicode(tagName)
        element.text = CDATA(element.text)

    def _create_item(self,key):
        db = self.__db['post'][key]
        """Creates an item from the Item element in the tree."""
        #the url link of the post
        linkpath = datetime.strptime(db['postContent']['pubDate'],'%a, %d %b %Y %H:%M%S +0000').strftime('%Y/%m/%d')
        print(db['postContent']['title'].text_content().encode('utf-8'))
        #finalLink = "%s/%s/%s" % (self.__url, linkpath, self._to_pin_yin(db['postContent']['title'].text_content())+unicode(self.__postId))
        finalLink = "%s/%s/%s" % (self.__url, linkpath, u'postid'+unicode(self.__postId))
        #item root
        item = SubElement(self.__channel,'item')
        #title
        title = SubElement(item,'title')
        title.text = db['postContent']['title'].text_content()
        #link
        link = SubElement(item,'link')
        link.text = finalLink
        #pubDate
        pubDate = SubElement(item,'pubDate')
        pubDate.text = db['postContent']['pubDate']
        #creator
        creator = SubElement(item,self.DC+'creator')
        creator.text = db['postContent']['creator'].text_content()
        #cata
        self._item_categories(item, db['postContent']['cata'])
        #tags
        self._item_tags(item, db['postContent']['tags'],self.__postId)
        #guid
        guid = SubElement(item,'guid',isPermaLink='true')
        guid.text = finalLink
        #desc
        SubElement(item,'description')
        #element(the post content)
        content=SubElement(item,self.CONTENT+'encoded')
        content.text=CDATA(tostring(db['postContent']['content'], encoding=unicode,pretty_print=True))
        #post id
        SubElement(item, self.WP+'post_id').text =unicode(self.__postId)
        wpPostDate=datetime.strptime(db['postContent']['pubDate'],'%a, %d %b %Y %H:%M%S +0000').strftime("%Y-%m-%d %H:%M:%S")
        #wp namespace stuff
        SubElement(item,self.WP+'post_date').text = wpPostDate
        SubElement(item,self.WP+'post_date_gmt').text = wpPostDate
        SubElement(item,self.WP+'comment_status').text = u'open'
        SubElement(item,self.WP+'post_name').text = self._to_pin_yin(db['postContent']['title'].text_content())+unicode(self.__postId)
        SubElement(item,self.WP+'status').text = u'publish'
        SubElement(item,self.WP+'post_parent').text = u'0'
        SubElement(item,self.WP+'menu_item').text = u'0'
        SubElement(item,self.WP+'post_type').text = u'post'
        #comments
        self._item_comments(item, db)

    def _item_categories(self, item, cata):
        """Links an item to categories."""

        element = SubElement(item,'category')
        element.text = CDATA(cata)

        elNice = SubElement(item,'category',domain="category", nicename=self._to_pin_yin(cata))

    def _item_tags(self, item, tagElList, item_id):
        """Links an item to tags."""

        for el in tagElList:
            tag=el.text_content()
            print(tag.encode('utf-8'))
            tagEl = SubElement(item,'category', domain="tag")
            tagEl.text = CDATA(tag)
            tagNiceEl=SubElement(item,'category', domain="tag",nicename=self._to_pin_yin(tag))
            tagNiceEl.text=CDATA(tag)

    def _item_comments(self, item, db):
        """Creates comments for an item."""
        for key in db['postComment'].keys():
            comment = SubElement(item,self.WP+'comment')
            #id
            SubElement(comment,self.WP+'comment_id').text= str(key)
            #author
            comment_author = SubElement(comment,self.WP+'comment_author')
            comment_author.text=CDATA(db['postComment'][key]['author'])
            #email
            SubElement(comment,self.WP+'comment_author_email').text=db['postComment'][key]['email']
            #url
            #leave url blank since it may contain old info
            #ip
            SubElement(comment,self.WP+'comment_author_IP').text=db['postComment'][key]['ip']
            #date
            SubElement(comment,self.WP+'comment_date').text=db['postComment'][key]['date']
            SubElement(comment,self.WP+'comment_date_gmt').text=db['postComment'][key]['date']
            #content
            SubElement(comment,self.WP+'comment_content').text=db['postComment'][key]['content']
            #static info
            SubElement(comment,self.WP+'comment_approved').text='1'
            SubElement(comment,self.WP+'comment_type')
            #parent
            SubElement(comment,self.WP+'comment_parent').text=unicode(db['postComment'][key]['parent'])


    def _process_catas(self):
        tempList = []
        for key in self.__db['post'].keys():
            #print 'key=%s, value=%s' % (key, self.db[0]['postComment'][key].encode('utf-8'))
            #print 'post=%s' % key
            if self.__db['post'][key].has_key('postContent'):
                folderName=self.__db['post'][key]['postContent']['cata']
                print(folderName.encode('utf-8'))
                if not folderName in tempList:
                    tempList += [folderName]
                    self._create_category(self._to_pin_yin(folderName),folderName)

    def _process_tags(self):
        elList=[]
        for key in self.__db['post'].keys():
            if self.__db['post'][key].has_key('postContent'):
                elList+=self.__db['post'][key]['postContent']['tags']

        tempList = []
        for list in elList:
            for l in list:
                if not l.text_content() in tempList:
                    tempList += [l.text_content()]

        for tag in tempList :
            self._create_tags(tag)

    def _process_contents(self):

        for key in self.__db['post'].keys():
            if self.__db['post'][key].has_key('postContent'):
                self.__postId+=1
                self._create_item(key)

    def _process_sites(self):
        title=SubElement(self.__channel, 'title')
        title.text = unicode(self.__title)

        link=SubElement(self.__channel,'link')
        link.text = unicode(self.__url)

        description=SubElement(self.__channel,'description')
        description.text = unicode(self.__desc)

        pubDate=SubElement(self.__channel,'pubDate')
        pubDate.text = unicode(datetime.utcnow().strftime('%a, %d %b %Y %H:%M%S +0000'))

        generator=SubElement(self.__channel,'generator')
        generator.text=unicode('all2w.py')

        language=SubElement(self.__channel,'language')
        language.text=unicode('cn')

    def _to_pin_yin(self, ustr):
        convert=CConvert()
        out=convert.convert(ustr.encode('gbk'))
        print(type(out[0]))
        py=string.replace(out[0]," ","").decode('gbk')
        print(py.encode('utf-8'))
        return py

    def export(self,db):
        """

        """
        self.__db=db
        self._process_sites()
        self._process_catas()
        self._process_tags()
        self._process_contents()

        output = tostring(self.__rss, pretty_print=True, xml_declaration=True, encoding='UTF-8')

        if self.__output_file:
            out = open(self.__output_file,'w')
            out.write(output)
            out.close()
        else:
            print output

class Road22:

    """
    The main class of the crawler.
    """
    def __init__(self, options):
        """
        Creates a new crawler.
        """
        # initialize
        self.__stranalyzer=options.analyzer
        self.__username = options.user
        seed=self.__createSeed(self.__username,self.__stranalyzer)
        authHandler = urllib2.HTTPBasicAuthHandler()
        opener = urllib2.build_opener(authHandler)
        urllib2.install_opener(opener)
        self.__linksToFetchAndCond = (dict(seed), Condition())
        self.__siteQueueAndCond = ([], Condition())
        self.__dbAndLock = ({}, Lock())
        print(options.maxPagesToCrawl)
        self.__maxPagesToCrawl = options.maxPagesToCrawl

        # create an analyzer
        if self.__stranalyzer=='ycool' :
            self.__analyzer = YculAnalyzer(self.__linksToFetchAndCond, self.__siteQueueAndCond,
                                   self.__dbAndLock, self.__username)
        elif self.__stranalyzer=='msn' :
            self.__analyzer = MSNAnalyzer(self.__linksToFetchAndCond, self.__siteQueueAndCond,
                                   self.__dbAndLock, self.__username)

        # create a controller
        self.__controller = Controller(self.__linksToFetchAndCond, self.__siteQueueAndCond, self.__analyzer,
                                            options)
        #create a exporter
        self.__exporter = Exporter(options)
    def __createSeed(self, user, analyzer):
        """
        Create a seed using {'user' : [url]} format.
        The url is generated by the type of the blog,
        which is marked by it's analyzer.
        """
        #if it is a ycool blog u want to move
        if analyzer=='ycool' :
            return {user : ['http://%s.ycool.com' % user]}
        #if it is a msn blog u want to move
        elif analyzer=='msn' :
            return {user : ['http://%s.spaces.msn.com/' % user]}
        else:
            raise Exception, ("I'm not able to understand the analyzer %s, contact to Lenciel to support it." % analyzer)
    def crawl(self):
        """
        Performs the crawling operation.
        """
        #beging analyzer and controller thread(actually called their run())
        self.__analyzer.start()
        self.__controller.start()
        #block until controller thread terminate
        self.__controller.join(3600)
        self.__analyzer.setStopCondition(True)
        self.__siteQueueAndCond[1].acquire()
        self.__siteQueueAndCond[1].notifyAll()
        self.__siteQueueAndCond[1].release()
        #block until analyzer thread terminate
        self.__analyzer.join()
        print "%d fetchers were useful" % self.__controller.getNumFetchersUsed()
        print("%d out of %d sites were succesfully crawles" %
                (len(self.__dbAndLock[0]['pages']),self.__maxPagesToCrawl))
        print "The pages that were succesfully crawled:"
        for s in self.__dbAndLock[0]['pages']:
            print self.__dbAndLock[0]['pages'][s].stringUrl

        self.__analyzer.report()

        self.__exporter.export(self.__dbAndLock[0])

def parseoptions(args):
    """Parses command line options used to creates a new crawler."""
    parser = OptionParser()
    parser.add_option("-u", "--user", default="lenciel",type="string",
                      help="The user name you used for old blog.")
    parser.add_option("-o", "--output", default="wrx.xml",type="string",
                      help="The filename where you want the output stored.")
    parser.add_option("-w", "--url", default="http://lenciel.cn",type="string",
                  help="The url you use for your new wordpress blog.")
    parser.add_option("-f", "--fetcherThreads", default=4,type=int,
                  help="The number of fetcher threads to use.")
    parser.add_option("-m", "--maxPagesToCrawl", default=800,type=int,
                  help="How many pages to crawl.")
    parser.add_option("-t", "--timeOut", default=15,type=int,
                  help="The socket timeout for loading a page.")
    parser.add_option("-d", "--delay", default=5,type=int,
                  help="The delay between crawls.")
    parser.add_option("-a", "--analyzer", default='ycool',type="string",
                  help="The class of the analyzer to use.")
    parser.add_option("-T", "--Title", default='Wordpress',type="string",
                  help="The title of the new weblog.")
    parser.add_option("-D", "--Description", default='Just another weblog',type="string",
                  help="The desc of the new weblog.")
    return parser.parse_args(args)[0]

def extractBaseUrl(stringUrl):
    """
    Extracts the domain name from a string URL and returns it.
    """
    u = urlparse.urlparse(stringUrl)
    return 'http://'+u[1]

if __name__ == '__main__':
    options = parseoptions(sys.argv)
    u = Road22(options)
    u.crawl()
