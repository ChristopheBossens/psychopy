#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Part of the PsychoPy library
# Copyright (C) 2018 Jonathan Peirce
# Distributed under the terms of the GNU General Public License (GPL).

from __future__ import absolute_import, print_function

import time
import wx
import wx.html2
import wx.lib.scrolledpanel as scrlpanel
from past.builtins import basestring

import git
import gitlab

try:
    import wx.adv as wxhl  # in wx 4
except ImportError:
    wxhl = wx  # in wx 3.0.2

from psychopy import logging, web, prefs
from psychopy.app import dialogs
from psychopy.projects import projectCatalog, projectsFolder, pavlovia
from psychopy.localization import _translate

BEGIN, END, COUNTING, COMPRESSING, WRITING, RECEIVING, RESOLVING, FINDING_SOURCES, CHECKING_OUT = \
         [1 << x for x in range(9)]
gitlabOperations = {BEGIN: "Starting...",
                    END: "Done",
                    COUNTING: "Counting",
                    COMPRESSING: "Compressing",
                    WRITING: "Writing",
                    RECEIVING: "Receiving",
                    RESOLVING: "Resolving",
                    FINDING_SOURCES: "Finding sources",
                    CHECKING_OUT: "Checking out",
                    }

"""
ProjectFrame could be removed? Or it could re-use the DetailsPanel? It currently
duplicates functionality - you can view the details of a project in either the
search or the ProjectEditor
"""


class PavloviaMenu(wx.Menu):
    app = None
    appData = None
    currentUser = None
    knownUsers = None
    searchDlg = None

    def __init__(self, parent):
        wx.Menu.__init__(self)
        self.parent = parent
        PavloviaMenu.app = parent.app
        keys = self.app.keys
        # from prefs fetch info about prev usernames and projects
        PavloviaMenu.appData = self.app.prefs.appData['projects']

        item = self.Append(wx.ID_ANY, _translate("Tell me more..."))
        parent.Bind(wx.EVT_MENU, self.onAbout, id=item.GetId())

        PavloviaMenu.knownUsers = pavlovia.knownUsers

        # sub-menu for usernames and login
        self.userMenu = wx.Menu()
        # if a user was previously logged in then set them as current
        if PavloviaMenu.appData[
            'pavloviaUser'] and not PavloviaMenu.currentUser:
            self.setUser(PavloviaMenu.appData['pavloviaUser'])
        for name in self.knownUsers:
            self.addToSubMenu(name, self.userMenu, self.onSetUser)
        self.userMenu.AppendSeparator()
        item = self.userMenu.Append(wx.ID_ANY,
                                    _translate("Log in to Pavlovia...\t{}")
                                    .format(keys['pavlovia_logIn']))
        parent.Bind(wx.EVT_MENU, self.onLogInPavlovia, id=item.GetId())
        self.AppendSubMenu(self.userMenu, _translate("User"))

        # search
        item = self.Append(wx.ID_ANY,
                           _translate("Search Pavlovia\t{}")
                           .format(keys['projectsFind']))
        parent.Bind(wx.EVT_MENU, self.onSearch, id=item.GetId())

        # new
        item = self.Append(wx.ID_ANY,
                           _translate("New...\t{}").format(keys['projectsNew']))
        parent.Bind(wx.EVT_MENU, self.onNew, id=item.GetId())

        # self.Append(wxIDs.projsSync, "Sync\t{}".format(keys['projectsSync']))
        # parent.Bind(wx.EVT_MENU, self.onSync, id=wxIDs.projsSync)

    def addToSubMenu(self, name, menu, function):
        item = menu.Append(wx.ID_ANY, name)
        self.parent.Bind(wx.EVT_MENU, function, id=item.GetId())

    def onAbout(self, event):
        wx.GetApp().followLink(event)

    def onSetUser(self, event):
        user = self.userMenu.GetLabelText(event.GetId())
        self.setUser(user)

    def setUser(self, user):
        if user == PavloviaMenu.currentUser:
            return  # nothing to do here. Move along please.
        PavloviaMenu.currentUser = user
        PavloviaMenu.appData['pavloviaUser'] = user
        if user in pavlovia.knownUsers:
            token = pavlovia.knownUsers[user]
            pavlovia.currentSession.setToken(token)
        else:
            self.onLogInPavlovia()

        if self.searchDlg:
            self.searchDlg.updateUserProjs()

    def onSync(self, event):
        pass  # TODO: create quick-sync from menu item

    def onSearch(self, event):
        PavloviaMenu.searchDlg = SearchFrame(app=self.parent.app)
        PavloviaMenu.searchDlg.Show()

    def onLogInPavlovia(self, event=None):
        # check known users list
        info = {}
        url, state = pavlovia.getAuthURL()
        dlg = OAuthBrowserDlg(self.parent, url, info=info)
        dlg.ShowModal()
        if info and state == info['state']:
            token = info['token']
            pavlovia.login(token)

    def onNew(self, event):
        """Create a new project
        """
        if pavlovia.currentSession.user.username:
            projEditor = ProjectEditor()
            projEditor.Show()
        else:
            infoDlg = dialogs.MessageDialog(parent=None, type='Info',
                                            message=_translate(
                                                "You need to log in"
                                                " to create a project"))
            infoDlg.Show()

    def onOpenFile(self, event):
        """Open project file from dialog
        """
        dlg = wx.FileDialog(parent=None,
                            message=_translate("Open local project file"),
                            style=wx.FD_OPEN,
                            wildcard=_translate(
                                "Project files (*.psyproj)|*.psyproj"))
        if dlg.ShowModal() == wx.ID_OK:
            projFile = dlg.GetPath()
            self.openProj(projFile)


# LogInDlgPavlovia
class OAuthBrowserDlg(wx.Dialog):
    """This class is used by to open the login (browser) window for pavlovia.org
    """
    defaultStyle = (wx.DEFAULT_DIALOG_STYLE | wx.DIALOG_NO_PARENT |
                    wx.TAB_TRAVERSAL | wx.RESIZE_BORDER)

    def __init__(self, parent, url, info,
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=defaultStyle):
        wx.Dialog.__init__(self, parent, pos=pos, size=size, style=style)
        self.tokenInfo = info
        # create browser window for authentication
        self.browser = wx.html2.WebView.New(self)
        self.browser.LoadURL(url)
        self.browser.Bind(wx.html2.EVT_WEBVIEW_LOADED, self.onNewURL)

        # do layout
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.browser, 1, wx.EXPAND, 10)
        self.SetSizer(sizer)
        self.SetSize((700, 700))

    def onNewURL(self, event):
        url = self.browser.CurrentURL
        if 'access_token=' in url:
            self.tokenInfo['token'] = self.getParamFromURL('access_token')
            self.tokenInfo['tokenType'] = self.getParamFromURL('token_type')
            self.tokenInfo['state'] = self.getParamFromURL('state')
            self.EndModal(wx.ID_OK)

    def getParamFromURL(self, paramName):
        url = self.browser.CurrentURL
        return url.split(paramName + '=')[1].split('&')[0]


class BaseFrame(wx.Frame):
    def __init__(self, *args, **kwargs):
        wx.Frame.__init__(self, *args, **kwargs)
        self.Center()
        # set up menu bar
        self.menuBar = wx.MenuBar()
        self.fileMenu = self.makeFileMenu()
        self.menuBar.Append(self.fileMenu, _translate('&File'))
        self.SetMenuBar(self.menuBar)

    def makeFileMenu(self):
        fileMenu = wx.Menu()
        app = wx.GetApp()
        keyCodes = app.keys
        # add items to file menu
        fileMenu.Append(wx.ID_CLOSE,
                        _translate("&Close View\t%s") % keyCodes['close'],
                        _translate("Close current window"))
        self.Bind(wx.EVT_MENU, self.closeFrame, id=wx.ID_CLOSE)
        # -------------quit
        fileMenu.AppendSeparator()
        fileMenu.Append(wx.ID_EXIT,
                        _translate("&Quit\t%s") % keyCodes['quit'],
                        _translate("Terminate the program"))
        self.Bind(wx.EVT_MENU, app.quit, id=wx.ID_EXIT)
        return fileMenu

    def closeFrame(self, event=None, checkSave=True):
        self.Destroy()

    def checkSave(self):
        """If the app asks whether everything is safely saved
        """
        return True  # for OK


class SearchFrame(BaseFrame):
    defaultStyle = (wx.DEFAULT_DIALOG_STYLE | wx.DIALOG_NO_PARENT |
                    wx.TAB_TRAVERSAL | wx.RESIZE_BORDER)

    def __init__(self, app, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=defaultStyle):
        title = _translate("Search for projects online")
        self.frameType = 'ProjectSearch'
        BaseFrame.__init__(self, None, -1, title, pos, size, style)
        self.app = app
        self.project = None

        # to show detail of current selection
        self.detailsPanel = DetailsPanel(parent=self)

        # create list of my projects (no search?)
        self.myProjectsPanel = ProjectListPanel(self, self.detailsPanel)

        # create list of searchable public projects
        self.publicProjectsPanel = ProjectListPanel(self, self.detailsPanel)
        self.publicProjectsPanel.setContents('')

        # sizers: on the left we have search boxes
        leftSizer = wx.BoxSizer(wx.VERTICAL)
        leftSizer.Add(wx.StaticText(self, -1, _translate("My Projects")),
                      flag=wx.EXPAND | wx.ALL, border=5)
        leftSizer.Add(self.myProjectsPanel,
                      proportion=1,
                      flag=wx.EXPAND | wx.BOTTOM | wx.LEFT | wx.RIGHT,
                      border=10)
        searchSizer = wx.BoxSizer(wx.HORIZONTAL)
        searchSizer.Add(wx.StaticText(self, -1, _translate("Search Public:")))
        self.searchTextCtrl = wx.TextCtrl(self, -1, "",
                                          style=wx.TE_PROCESS_ENTER)
        self.searchTextCtrl.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        searchSizer.Add(self.searchTextCtrl, flag=wx.EXPAND)
        leftSizer.Add(searchSizer)
        tagsSizer = wx.BoxSizer(wx.HORIZONTAL)
        tagsSizer.Add(wx.StaticText(self, -1, _translate("Tags:")))
        self.tagsTextCtrl = wx.TextCtrl(self, -1, "psychopy,",
                                        style=wx.TE_PROCESS_ENTER)
        self.tagsTextCtrl.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        tagsSizer.Add(self.tagsTextCtrl, flag=wx.EXPAND)
        leftSizer.Add(tagsSizer)
        leftSizer.Add(self.publicProjectsPanel,
                      proportion=1,
                      flag=wx.EXPAND | wx.BOTTOM | wx.LEFT | wx.RIGHT,
                      border=10)

        self.mainSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.mainSizer.Add(leftSizer, flag=wx.EXPAND, proportion=1, border=5)
        self.mainSizer.Add(self.detailsPanel, flag=wx.EXPAND, proportion=1,
                           border=5)
        self.SetSizerAndFit(self.mainSizer)

        aTable = wx.AcceleratorTable([(0, wx.WXK_ESCAPE, wx.ID_CANCEL),
                                      ])
        self.SetAcceleratorTable(aTable)
        self.Show()  # show the window before doing search/updates
        self.updateUserProjs()  # update the info in myProjectsPanel

    def updateUserProjs(self):
        if not pavlovia.currentSession.user:
            self.myProjectsPanel.setContents(
                _translate("No user logged in"))
        else:
            self.myProjectsPanel.setContents(
                _translate("Searching projects for user {} ...")
                    .format(pavlovia.currentSession.user.username))
            self.Update()
            wx.Yield()
            myProjs = pavlovia.currentSession.findUserProjects()
            self.myProjectsPanel.setContents(myProjs)

    def onSearch(self, evt):
        searchStr = self.searchTextCtrl.GetValue()
        tagsStr = self.tagsTextCtrl.GetValue()
        session = pavlovia.currentSession
        self.publicProjectsPanel.setContents(_translate("searching..."))
        self.publicProjectsPanel.Update()
        wx.Yield()
        projs = session.findProjects(search_str=searchStr, tags=tagsStr)
        self.publicProjectsPanel.setContents(projs)


class ProjectListPanel(scrlpanel.ScrolledPanel):
    """A scrollable panel showing a list of projects. To be used within the
    Project Search dialog
    """

    def __init__(self, parent, detailsPanel):
        scrlpanel.ScrolledPanel.__init__(self, parent, -1, size=(450, 200),
                                         style=wx.SUNKEN_BORDER)
        self.parent = parent
        self.knownProjects = {}
        self.projList = []
        self.mainSizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.mainSizer)  # don't do Fit
        self.mainSizer.Fit(self)

        self.SetAutoLayout(True)
        self.SetupScrolling()

    def setContents(self, projects):
        self.DestroyChildren()  # start with a clean slate

        if isinstance(projects, basestring):
            # just text for a window so display
            self.mainSizer.Add(
                wx.StaticText(self, -1, projects),
                flag=wx.EXPAND | wx.ALL, border=5,
            )
        else:
            # a list of projects
            self.projView = wx.ListCtrl(parent=self,
                                        style=wx.LC_REPORT | wx.LC_SINGLE_SEL)

            # Give it some columns.
            # The ID col we'll customize a bit:
            self.projView.InsertColumn(0, 'owner')
            self.projView.InsertColumn(1, 'name')
            self.projView.InsertColumn(1, 'description')
            self.projList = []
            for index, thisProj in enumerate(projects):
                if not hasattr(thisProj, 'id'):
                    continue
                self.projView.Append([thisProj.owner, thisProj.name,
                                      thisProj.description])
                self.projList.append(thisProj)
            # set the column sizes *after* adding the items
            self.projView.SetColumnWidth(0, wx.LIST_AUTOSIZE)
            self.projView.SetColumnWidth(1, wx.LIST_AUTOSIZE)
            self.projView.SetColumnWidth(2, wx.LIST_AUTOSIZE)
            self.mainSizer.Add(self.projView,
                               flag=wx.EXPAND | wx.ALL,
                               proportion=1, border=5, )
            self.Bind(wx.EVT_LIST_ITEM_SELECTED,
                      self.onChangeSelection)

        self.FitInside()

    def onChangeSelection(self, event):
        proj = self.projList[event.GetIndex()]
        self.parent.detailsPanel.setProject(proj)


class DetailsPanel(scrlpanel.ScrolledPanel):

    def __init__(self, parent, noTitle=False,
                 style=wx.VSCROLL | wx.NO_BORDER):
        scrlpanel.ScrolledPanel.__init__(self, parent, -1, style=style)
        self.parent = parent
        self.app = self.parent.app
        self.project = {}
        self.noTitle = noTitle

        # self.syncPanel = SyncStatusPanel(parent=self, id=wx.ID_ANY)
        # self.syncPanel.Hide()

        if not noTitle:
            self.title = wx.StaticText(parent=self, id=-1,
                                       label="", style=wx.ALIGN_CENTER)
            font = wx.Font(18, wx.DECORATIVE, wx.NORMAL, wx.BOLD)
            self.title.SetFont(font)

        # if we've synced before we should know the local location
        self.localFolder = wx.StaticText(
            parent=self, id=-1,
            label="Local root: ")
        self.browseLocalBtn = wx.Button(self, wx.ID_ANY, "Browse...")
        self.browseLocalBtn.Bind(wx.EVT_BUTTON, self.onBrowseLocalFolder)

        # remote attributes
        self.url = wxhl.HyperlinkCtrl(parent=self, id=-1,
                                      label="https://pavlovia.org",
                                      url="https://pavlovia.org",
                                      style=wxhl.HL_ALIGN_LEFT,
                                      )
        self.description = wx.StaticText(parent=self, id=-1,
                                         label=_translate(
                                             "Select a project for details"))
        self.tags = wx.StaticText(parent=self, id=-1,
                                  label="")
        self.visibility = wx.StaticText(parent=self, id=-1,
                                        label="")

        self.syncButton = wx.Button(self, -1, _translate("Sync..."))
        self.syncButton.Enable(False)
        self.syncButton.Bind(wx.EVT_BUTTON, self.onSyncButton)

        # layout
        # sizers: on the right we have detail
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.sizer.Add(wx.StaticText(self, -1, _translate("Project Info")),
                       flag=wx.ALL,
                       border=5)
        if not noTitle:
            self.sizer.Add(self.title, border=5,
                           flag=wx.ALL | wx.ALIGN_CENTER)
        self.sizer.Add(self.url, border=5,
                       flag=wx.ALL | wx.CENTER)
        localFolderSizer = wx.BoxSizer(wx.HORIZONTAL)
        localFolderSizer.Add(self.localFolder, border=5,
                             flag=wx.ALL | wx.EXPAND),
        localFolderSizer.Add(self.browseLocalBtn, border=5,
                             flag=wx.ALL | wx.EXPAND)
        self.sizer.Add(localFolderSizer, border=5, flag=wx.ALL | wx.EXPAND)

        self.sizer.Add(self.tags, border=5, flag=wx.ALL | wx.EXPAND)
        self.sizer.Add(self.visibility, border=5, flag=wx.ALL | wx.EXPAND)
        self.sizer.Add(wx.StaticLine(self, -1, style=wx.LI_HORIZONTAL),
                       flag=wx.ALL | wx.EXPAND)
        self.sizer.Add(self.description, border=10, flag=wx.ALL | wx.EXPAND)

        self.sizer.Add(wx.StaticLine(self, -1, style=wx.LI_HORIZONTAL),
                       flag=wx.ALL | wx.EXPAND)
        self.sizer.Add(self.syncButton,
                       flag=wx.ALL | wx.RIGHT, border=5)

        self.SetSizer(self.sizer)
        self.SetupScrolling()
        self.Layout()
        self.Bind(wx.EVT_SIZE, self.onResize)

    def setProject(self, project):
        if not isinstance(project, pavlovia.PavloviaProject):
            # e.g. '382' or 382
            project = pavlovia.currentSession.projectFromID(project)
        if project is None:
            return  # we're done
        self.project = project

        if not self.noTitle:
            self.title.SetLabel("{} / {}".format(project.owner, project.name))

        # url
        self.url.SetLabel(self.project.web_url)
        self.url.SetURL(self.project.web_url)

        # public / private
        self.description.SetLabel(project.attributes['description'])
        if project.visibility in ['public', 'internal']:
            visib = "Public"
        else:
            visib = "Private"
        self.visibility.SetLabel(_translate("Visibility: {}").format(visib))

        # do we have a local location?
        localFolder = project['local']
        if not localFolder:
            localFolder = "<not yet synced>"
        self.localFolder.SetLabel("Local root: {}".format(localFolder))

        # should sync be enabled?
        perms = project.permissions['project_access']
        if type(perms) == dict:
            perms = perms['access_level']
        if (perms is not None) and perms >= pavlovia.permissions['developer']:
            self.syncButton.SetLabel('Sync...')
        else:
            self.syncButton.SetLabel('Fork + sync...')
        self.syncButton.Enable(True)  # now we have a project we should enable

        while None in project.tags:
            project.tags.remove(None)
        self.tags.SetLabel(_translate("Tags:") + " " + ", ".join(project.tags))
        # call onResize to get correct wrapping of description box and title
        self.onResize()

    def onResize(self, evt=None):
        if self.project is None:
            return
        w, h = self.GetSize()
        # if it hasn't been created yet then we won't have attributes
        if hasattr(self.project, 'attributes'):
            self.description.SetLabel(self.project.attributes['description'])
            self.description.Wrap(w - 20)
        # noTitle in some uses of the detailsPanel
        if not self.noTitle and 'name' in self.project:
            self.title.SetLabel(self.project.name)
            self.title.Wrap(w - 20)
        self.Layout()

    def onSyncButton(self, event):
        if self.project is None:
            raise AttributeError("User pressed the sync button with no "
                                 "current project existing.")

        # if project.local doesn't exist, or is empty
        if 'local' not in self.project or not self.project.local:
            # we first need to choose a location for the repository
            newPath = setLocalPath(self, self.project)
            self.localFolder.SetLabel(
                label="Local root: {}".format(newPath))
        #
        # progHandler = ProgressHandler(syncPanel=self.syncPanel)
        # self.syncPanel.Show()
        # self.Update()
        # self.Layout()
        # wx.Yield()
        # self.project.sync(progressHandler=progHandler)
        # time.sleep(0.1)
        # self.syncPanel.Hide()
        #
        #
        syncPanel = SyncStatusPanel(parent=self, id=wx.ID_ANY)
        self.sizer.Add(syncPanel, border=5,
                       flag=wx.ALL | wx.RIGHT)
        self.sizer.Layout()
        progHandler = ProgressHandler(syncPanel=syncPanel)
        wx.Yield()
        self.project.sync(progressHandler=progHandler)
        syncPanel.Destroy()
        self.sizer.Layout()
        #
        #
        # syncFrame = SyncFrame(parent=self, id=wx.ID_ANY, project=self.project)
        # progHandler = ProgressHandler(syncPanel=syncFrame.syncPanel)
        # syncFrame.Show()
        # self.project.sync(progressHandler=progHandler)
        # syncFrame.
        # time.sleep(0.1)

    def onBrowseLocalFolder(self, evt):
        newPath = setLocalPath(self, self.project)
        if newPath:
            self.localFolder.SetLabel(
                label="Local root: {}".format(newPath))
            self.Update()


class ProjectEditor(BaseFrame):
    def __init__(self, parent=None, id=-1, projId="", *args, **kwargs):
        pass  # to do for creating project
        """
        BaseFrame.__init__(self, None, -1, *args, **kwargs)
        panel = wx.Panel(self, -1, style=wx.TAB_TRAVERSAL)
        # when a project is succesffully created these will be populated
        self.project = None
        self.projInfo = None

        if projId:
            # edit existing project
            self.isNew = False
        else:
            self.isNew = True

        # create the controls
        titleLabel = wx.StaticText(panel, -1, _translate("Title:"))
        self.titleBox = wx.TextCtrl(panel, -1, size=(400, -1))
        nameLabel = wx.StaticText(panel, -1,
                                  _translate("Name \n(for local id):"))
        self.nameBox = wx.TextCtrl(panel, -1, size=(400, -1))
        descrLabel = wx.StaticText(panel, -1, _translate("Description:"))
        self.descrBox = wx.TextCtrl(panel, -1, size=(400, 200),
                                    style=wx.TE_MULTILINE | wx.SUNKEN_BORDER)
        tagsLabel = wx.StaticText(panel, -1,
                                  _translate("Tags (comma separated):"))
        self.tagsBox = wx.TextCtrl(panel, -1, size=(400, 100),
                                   value="PsychoPy, Builder, Coder",
                                   style=wx.TE_MULTILINE | wx.SUNKEN_BORDER)
        publicLabel = wx.StaticText(panel, -1, _translate("Public:"))
        self.publicBox = wx.CheckBox(panel, -1)
        # buttons
        if self.isNew:
            buttonMsg = _translate("Create project on OSF")
        else:
            buttonMsg = _translate("Submit changes to OSF")
        updateBtn = wx.Button(panel, -1, buttonMsg)
        updateBtn.Bind(wx.EVT_BUTTON, self.submitChanges)

        # do layout
        mainSizer = wx.FlexGridSizer(cols=2, rows=6, vgap=5, hgap=5)
        mainSizer.AddMany([(titleLabel, 0, wx.ALIGN_RIGHT), self.titleBox,
                           (nameLabel, 0, wx.ALIGN_RIGHT),
                           (self.nameBox, 0, wx.EXPAND),
                           (descrLabel, 0, wx.ALIGN_RIGHT), self.descrBox,
                           (tagsLabel, 0, wx.ALIGN_RIGHT), self.tagsBox,
                           (publicLabel, 0, wx.ALIGN_RIGHT), self.publicBox,
                           (0, 0), (updateBtn, 0, wx.ALIGN_RIGHT)])
        border = wx.BoxSizer()
        border.Add(mainSizer, 0, wx.ALL, 10)
        panel.SetSizerAndFit(border)
        self.Fit()

    def submitChanges(self, evt=None):
        session = wx.GetApp().pavloviaSession
        d = {}
        d['title'] = self.titleBox.GetValue()
        d['name'] = self.nameBox.GetValue()
        d['descr'] = self.descrBox.GetValue()
        d['public'] = self.publicBox.GetValue()
        # tags need splitting and then
        tagsList = self.tagsBox.GetValue().split(',')
        d['tags'] = []
        for thisTag in tagsList:
            d['tags'].append(thisTag.strip())
        if self.isNew:
            newProject = session.create_project(title=d['title'],
                                                descr=d['descr'],
                                                tags=d['tags'],
                                                public=d['public'])

            projFrame = ProjectFrame(parent=None, id=-1, title=d['title'])
            projFrame.setProject(newProject)
            projFrame.nameCtrl.SetValue(d['name'])
            projFrame.Show()
        else:  # to be done
            newProject = session.update_project(id, title=d['title'],
                                                descr=d['descr'],
                                                tags=d['tags'],
                                                public=d['public'])
        # store in self in case we're being watched
        self.project = newProject
        self.projInfo = d
        self.Destroy()  # kill the dialog
        """


class SyncFrame(wx.Frame):
    def __init__(self, parent, id, project):
        title = "{} / {}".format(project.owner, project.title)
        style = wx.DEFAULT_FRAME_STYLE ^ wx.RESIZE_BORDER
        wx.Frame.__init__(self, parent=None, id=id, style=style,
                          title=title)
        self.parent = parent
        self.project = project

        # create the sync panel and start sync(!)
        self.syncPanel = SyncStatusPanel(parent=self, id=wx.ID_ANY)
        self.progHandler = ProgressHandler(syncPanel=self.syncPanel)
        # layout the controls
        self.mainSizer = wx.BoxSizer()
        self.mainSizer.Add(self.syncPanel, wx.ALL, border=10)
        self.SetSizerAndFit(self.mainSizer)
        self.SetAutoLayout(True)
        # self.SetMaxSize(self.Size)
        # self.SetMinSize(self.Size)

        self.Show()
        wx.Yield()

        self.project.sync(progressHandler=self.progHandler)


class SyncStatusPanel(wx.Panel):
    def __init__(self, parent, id, size=(300, 250), *args, **kwargs):
        # init super classes
        wx.Panel.__init__(self, parent, id, size=size, *args, **kwargs)
        # set self properties
        self.parent = parent
        self.statusMsg = wx.StaticText(self, -1, "Synchronising...")
        self.progBar = wx.Gauge(self, -1, range=1, size=(200, -1))

        self.mainSizer = wx.BoxSizer(wx.VERTICAL)
        self.mainSizer.Add(self.statusMsg, wx.ALL | wx.CENTER, border=10)
        self.mainSizer.Add(self.progBar, wx.ALL, border=10)
        self.SetSizerAndFit(self.mainSizer)

        self.SetAutoLayout(True)
        self.Layout()

    def reset(self):
        self.progBar.SetRange(1)
        self.progBar.SetValue(0)


class ProgressHandler(git.remote.RemoteProgress):
    """We can't override the update() method so we have to create our own
    subclass for this"""

    def __init__(self, syncPanel, *args, **kwargs):
        git.remote.RemoteProgress.__init__(self, *args, **kwargs)
        self.syncPanel = syncPanel
        self.frame = syncPanel.parent
        self.t0 = None

    def setStatus(self, msg):
        self.syncPanel.statusMsg.SetLabel(msg)

    def update(self, op_code=0, cur_count=1, max_count=None, message=''):
        """Update the statusMsg and progBar for the syncPanel
        """
        if not self.t0:
            self.t0 = time.time()
        if op_code in ['10', 10]:  # indicates complete
            label = "Successfully synced"
        else:
            label = self._cur_line.split(':')[1]
            print("{:.5f}: {}".format(time.time()-self.t0, self._cur_line))
            label = self._cur_line
        self.setStatus(label)
        try:
            maxCount = int(max_count)
        except:
            maxCount = 1
        try:
            currCount = int(cur_count)
        except:
            currCount = 1

        self.syncPanel.progBar.SetRange(maxCount)
        self.syncPanel.progBar.SetValue(currCount)
        self.syncPanel.Refresh()
        self.syncPanel.Update()
        self.syncPanel.mainSizer.Layout()
        wx.Yield()
        time.sleep(0.001)


def setLocalPath(parent, project):
    """Open a DirDialog and set the project local folder to that specified

    Returns
    ----------

    None for no change and newPath if this has changed from previous
    """
    if project and 'local' in project:
        origPath = project.local
    else:
        origPath = None
    # create the dialog
    dlg = wx.DirDialog(
        parent,
        message=_translate(
            "Choose/create the root location for the synced project"))
    if dlg.ShowModal() == wx.ID_OK:
        newPath = dlg.GetPath()
        if newPath != origPath:
            project.local = newPath
            return newPath
    return None
