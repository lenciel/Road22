"""
This package is an implementation of an content detection analyzer
"""

from road22 import *
import re
import logging
from lxml.cssselect import CSSSelector
from datetime import datetime
from string import replace,strip


class YculAnalyzer(StupidAnalyzer):
    """
    This is a concrete analyzer which used together with the Orchid crawler
    to detect malicious web pages based on a given set of rules.
    """
    def __init__(self, linksToFetchAndCond, siteQueueAndCond, db, usrName):
        """
        Creates a new malicious content analyzer.
        @param rules: a list of Rule objects to be applied against crawled sites.
        """
        StupidAnalyzer.__init__(self, linksToFetchAndCond, siteQueueAndCond, db, usrName)
        self.__newArchiveLinksToCrawl = []
        self.__newOtherLinksToCrawl = []
        self.__postCounter = {}
        self.__counter = 0
        self.__userName=usrName
        self.__commentId = 0
        self.__stopSign = 1

    def analyzeSite(self, db, site, usrName):
        """
        Applies all the available rules to the given site and extracts the
        links that we intend to crawl. Currently we follow regular ('<a...'), frame,
        iframe and script links.
        """
        # initialize the db during the first call
        if not db.has_key('post'):
            db['post'] = {}


        # extract the links that we want to follow some day
        linkPattern = re.compile(r'\bhttp://'+usrName+'\.ycool\.com/post\.(\d+)\.html$', re.I)
        archivePattern = re.compile(r'\bhttp://'+usrName+'\.ycool\.com/archive(.p\d+)?\.html', re.I)
        commentPattern = re.compile(r'\bhttp://'+usrName+'\.ycool\.com/followups\.(\d+)\.html$', re.I)

        # mark the site to avoid crawling it in the future
        db['pages'][site.stringUrl] = site
        db['crawled'][site.stringUrl] = True
        #print("site.stringUrl"+site.stringUrl)
        #print(db['crawled'])

        if archivePattern.search(site.stringUrl):
            for link in site.parsedContent.iterlinks():
                 #print("link:",link)
                 if (not db['crawled'].has_key(link[2])):
                         #db['crawled'][link[2]]=True
                         if linkPattern.search(link[2]):
                             #print('------1---------')
                             #print('content link: '+link[2])
                             self.__newOtherLinksToCrawl+=[link[2]]
                             self.__newOtherLinksToCrawl+=[self.__getCommentLink(link[2])]
                         elif archivePattern.search(link[2]):
                             #print('------1---------')
                             #print('archive link: '+link[2])
                             self.__newArchiveLinksToCrawl+= [link[2]]
        #print('------5 links before remove dup---------')
        #print(self.__newOtherLinksToCrawl)
        self.__newOtherLinksToCrawl=self.__removeDup(db,self.__newOtherLinksToCrawl)
        #print('------5 links---------')
        #print(self.__newOtherLinksToCrawl)

        self.__newArchiveLinksToCrawl=self.__removeDup(db,self.__newArchiveLinksToCrawl)
        #print('------5 archives---------')
        #print(self.__newArchiveLinksToCrawl)
        # put the rawContent to db if it is a post
        if linkPattern.search(site.stringUrl):
            #print(site.stringUrl)
            entry =linkPattern.search(site.stringUrl).group(1)
            if not db['post'].has_key(entry):
                db['post'][entry] = {}
            if not db['post'][entry].has_key('postContent'):
                db['post'][entry]['postContent'] = {}
            leftFrame = self.__getLeftFrame(site.parsedContent)
            db['post'][entry]['postContent']['title'] = self.__getTitle(leftFrame)
            db['post'][entry]['postContent']['content'] = self.__getContent(leftFrame)
            db['post'][entry]['postContent']['creator'] = self.__getCreator(leftFrame)
            db['post'][entry]['postContent']['pubDate'] = self.__getpubDate(leftFrame)
            db['post'][entry]['postContent']['tags'] = self.__getTags(leftFrame)
            db['post'][entry]['postContent']['cata'] = self.__getCata(leftFrame)


            #db['post'][linkPattern.search(site.stringUrl).group(1)]['postContent'] = self.__getLeftFrame(site.parsedContent)
            #print(site.stringUrl)

        if commentPattern.search(site.stringUrl):
            #print('----1----',site.stringUrl )
            entry =commentPattern.search(site.stringUrl).group(1)
            if not db['post'].has_key(entry):
                db['post'][entry] = {}
            if not db['post'][entry].has_key('postComment'):
                db['post'][entry]['postComment'] = {}

            commentList = self.__getCommentList(self.__getLeftFrame(site.parsedContent))
            for e in commentList :
                self.__commentId+=1
                id = str(self.__commentId)

                replyEl = self.__getMyReply(e)
                el = self.__getCommentInfo(e)
                ip =self.__getCommentIp(el)[0]
                date = self.__getCommentDate(el)[0]
                if not db['post'][entry]['postComment'].has_key(id):
                    db['post'][entry]['postComment'][id]={}
                    print('--------a comment-----------')
                    db['post'][entry]['postComment'][id]['author']= strip(e.getparent().getparent()[0].text_content())
                    db['post'][entry]['postComment'][id]['ip']= ip
                    db['post'][entry]['postComment'][id]['date']= date+u':00'
                    db['post'][entry]['postComment'][id]['content']=tostring(e[0], encoding=unicode,method='text',pretty_print=True)
                    #db['post'][entry]['postComment'][id]['content']=e[0].text
                    db['post'][entry]['postComment'][id]['parent']=0
                    db['post'][entry]['postComment'][id]['email']='x@y.com'
                    print("conent %s date %s id %s "  % \
                          (db['post'][entry]['postComment'][id]['content'].encode('utf-8'),\
                           date.encode('utf-8'),id))

                if len(replyEl)>0:
                    self.__commentId+=1
                    childkey=str(self.__commentId)
                    if not db['post'][entry]['postComment'].has_key(childkey):
                        db['post'][entry]['postComment'][childkey]={}
                        print('--------a reply-----------')
                        print('reply id %s' % childkey )
                        db['post'][entry]['postComment'][childkey]['author']=self.__userName
                        db['post'][entry]['postComment'][childkey]['ip']='127.0.0.1'
                        db['post'][entry]['postComment'][childkey]['date']=date+u':03'
                        db['post'][entry]['postComment'][childkey]['email']='x@y.com'
                        db['post'][entry]['postComment'][childkey]['content']=tostring(replyEl[0], method='text',encoding=unicode,pretty_print=True)
                        db['post'][entry]['postComment'][childkey]['parent']=self.__commentId-1
                        print("conent %s date %s parent %s" % \
                        (db['post'][entry]['postComment'][childkey]['content'].encode('utf-8'), \
                        date.encode('utf-8'), \
                        db['post'][entry]['postComment'][childkey]['parent']))

    def __removeDup(self,db,links):
        tempList = []
        for l in links:
            db['crawled'][l] = True
            if not l in tempList:
                #print('------3---------')
                #print(l)
                tempList += [l]
                #print tempList

        return tempList

    def __getCommentLink(self, strLink):
        rcomment = re.compile(r"\bpost\b")
        return rcomment.sub("followups", strLink)

    def __getLeftFrame(self, doc):
        '''return el of left frame code'''
        selLeftFrame = CSSSelector('td.leftframe')
        return selLeftFrame(doc)[0]

    def __getTitle(self, doc):
        '''return el of content title code'''
        selTitle = CSSSelector('a.post_title')
        return selTitle(doc)[0]

    def __getContent(self, doc):
        '''return el of content code'''
        selContent = CSSSelector('div.post_content')
        return selContent(doc)[0]

    def __getCreator(self, doc):
        '''return el of creator code'''
        selUser = CSSSelector('span.post_user')
        return selUser(doc)[0][0]

    def __getpubDate(self, doc):
        '''return unicode string of pubDate'''
        selPostTime = CSSSelector('span.post_time')
        cleanDate=strip(replace(selPostTime(doc)[0].text_content(),"@",""))
        return datetime.strptime(cleanDate,"%Y-%m-%d %H:%M").strftime('%a, %d %b %Y %H:%M%S +0000')

    def __getTags(self, doc):
        '''return a el list of tag List'''
        selTags = CSSSelector('a.post_tags_link')
        return selTags(doc)

    def __getCata(self, doc):
        '''return a unicode string of cata'''
        selFolder = CSSSelector('a.post_folder')
        if len(selFolder(doc))>0:
            return selFolder(doc)[0].text_content()
        else :
            return u'Default'
    def __getCommentList(self,doc):
        selCommentList = CSSSelector('blockquote.followup_content')
        return selCommentList(doc)
    def __getMyReply(self,doc):
        selPostCommentReply = CSSSelector('blockquote.followup_reply')
        return selPostCommentReply(doc)
    def __getCommentInfo(self,doc):
        selUser = CSSSelector('span.post_user')
        return selUser(doc.getparent())[0]
    def __getCommentIp(self,doc):
        ips = re.findall('(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})', doc.text_content())
        if len(ips)==0:
            ips =['222.222.222.222']
        return ips
    def __getCommentDate(self,doc):
        dates = re.findall('\d{4}-\d{2}-\d{2} \d{2}:\d{2}', doc.text_content())
        if len(dates)==0:
            dates =['1998-01-01 01:01']
        return dates
    def addSiteToFetchQueue(self, lfs):
        """
        Add the sites we extracted in analyzeSite to the "to fetch" queue.
        """
        logging.debug("Adding to lfs")
        if lfs.has_key('archive'):
            lfs['archive'] = self.__newArchiveLinksToCrawl
            #print('--------6--------')
            #print[lfs['archive']]
        if lfs.has_key('content'):
            lfs['content'] = self.__newOtherLinksToCrawl
            #print('--------6--------')
            #print[lfs['content']]

    def selectNextUrl(self):
        """
        Select the next url to crawl to. This is done by selecting
        a random domain and then taking one page from it's queue.
        """
        #get the linksToFetch dict
        toFetchQueue = self.linksToFetchAndCond[0]

        #linkType = toFetchQueue.keys()
        curUrl = 'fake link used to feed the free crawler ' +str(self.__counter)
        #fisrt time get the archive link
        if toFetchQueue.has_key(self.__userName):
            toFetchQueue['archive'] = [toFetchQueue[self.__userName][0]+'/archive.html']
            toFetchQueue['content'] = []
            toFetchQueue.pop(self.__userName)

        # get one link from it's queue
        if toFetchQueue.has_key('archive'):
            if len(toFetchQueue['archive']) >0:
                curUrl = toFetchQueue['archive'].pop()
            elif len(toFetchQueue['archive']) == 0:
                toFetchQueue.pop('archive')
                #self.__feedFreeFetcher+=1
                #curUrl = '1) feed the free crawler ' + str(self.__feedFreeFetcher)
                if toFetchQueue.has_key('content'):
                    if len(toFetchQueue['content'])>0:
                        curUrl = toFetchQueue['content'].pop()
                    elif len(toFetchQueue['content'])==0:
                        toFetchQueue.pop('content')
        elif toFetchQueue.has_key('content'):
                if len(toFetchQueue['content'])>0:
                    curUrl = toFetchQueue['content'].pop()
                elif len(toFetchQueue['content'])==0:
                    toFetchQueue.pop('content')
                    curUrl= 'everything you want is crawled'
                    self.__stopSign = -1
                            #self.__feedFreeFetcher+=1
                        #curUrl = '2) feed the free crawler ' + str(self.__feedFreeFetcher)

        self.__counter+=1
#            #curUrl = '3) feed the free crawler ' + str(self.__feedFreeFetcher)
#        print(self.__counter)
#        if self.__counter == 5:
#            self.__stopSign = -1
        print("selected:"+curUrl)
        return curUrl
    def getStopSign(self):
        return self.__stopSign
    def report(self):
        """
        Logs the results of the crawl.
        """
        logging.info('Report:')
        logging.info('============')
        logging.info('pages crawled: %d' % len(self.db[0]['crawled']))
        logging.info('Post Info:')
        for key in self.db[0]['post'].keys():
            #print 'key=%s, value=%s' % (key, self.db[0]['postComment'][key].encode('utf-8'))
            print 'post=%s' % key


