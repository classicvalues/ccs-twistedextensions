# -*- test-case-name: twext.who.test.test_util -*-
##
# Copyright (c) 2013 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

from __future__ import print_function

"""
OpenDirectory directory service implementation.
"""

__all__ = [
    "OpenDirectoryError",
    "DirectoryService",
    "DirectoryRecord",
]

from itertools import chain

from twext.python.log import Logger
from twisted.python.constants import Names, NamedConstant
from twisted.python.constants import Values, ValueConstant

from twext.who.idirectory import (
    DirectoryServiceError, QueryNotSupportedError,
    FieldName as BaseFieldName,
    RecordType as BaseRecordType,
)
from twext.who.directory import (
    DirectoryService as BaseDirectoryService,
    DirectoryRecord as BaseDirectoryRecord,
)
from twext.who.expression import CompoundExpression, Operand
from twext.who.expression import MatchExpression, MatchType, MatchFlags
from twext.who.util import iterFlags, ConstantsContainer

from opendirectory import (
    ODError, odInit,
    getNodeAttributes,
    queryRecordsWithAttribute_list,
)



#
# Exceptions
#

class OpenDirectoryError(DirectoryServiceError):
    """
    OpenDirectory error.
    """



#
# Constants
#

class FieldName(Names):
    searchPath = NamedConstant()
    searchPath.description = "search path"
    searchPath.multiValue = False

    metaNodeLocation = NamedConstant()
    metaNodeLocation.description = "source OD node"
    metaNodeLocation.multiValue = False

    metaRecordName = NamedConstant()
    metaRecordName.description = "meta record name"
    metaRecordName.multiValue = False


#
# OD Constants
#

class ODSearchPath(Values):
    local = ValueConstant("/Local/Default")
    search = ValueConstant("/Search")



class ODRecordType(Values):
    user = ValueConstant("dsRecTypeStandard:Users")
    user.recordType = BaseRecordType.user

    group = ValueConstant("dsRecTypeStandard:Groups")
    group.recordType = BaseRecordType.group


    @classmethod
    def fromRecordType(cls, recordType):
        if not hasattr(cls, "_recordTypeByRecordType"):
            cls._recordTypeByRecordType = dict((
                (recordType.recordType, recordType)
                for recordType in cls.iterconstants()
            ))

        return cls._recordTypeByRecordType.get(recordType, None)



class ODAttribute(Values):
    searchPath = ValueConstant("dsAttrTypeStandard:SearchPath")
    searchPath.fieldName = FieldName.searchPath

    recordType = ValueConstant("dsAttrTypeStandard:RecordType")
    recordType.fieldName = BaseFieldName.recordType

    uid = ValueConstant("dsAttrTypeStandard:GeneratedUID")
    uid.fieldName = BaseFieldName.uid

    guid = ValueConstant("dsAttrTypeStandard:GeneratedUID")
    guid.fieldName = BaseFieldName.guid

    shortName = ValueConstant("dsAttrTypeStandard:RecordName")
    shortName.fieldName = BaseFieldName.shortNames

    fullName = ValueConstant("dsAttrTypeStandard:RealName")
    fullName.fieldName = BaseFieldName.fullNames

    emailAddress = ValueConstant("dsAttrTypeStandard:EMailAddress")
    emailAddress.fieldName = BaseFieldName.emailAddresses

    metaNodeLocation = ValueConstant(
        "dsAttrTypeStandard:AppleMetaNodeLocation"
    )
    metaNodeLocation.fieldName = FieldName.metaNodeLocation

    metaRecordName = ValueConstant("dsAttrTypeStandard:AppleMetaRecordName")
    metaRecordName.fieldName = FieldName.metaRecordName


    @classmethod
    def fromFieldName(cls, fieldName):
        if not hasattr(cls, "_attributesByFieldName"):
            cls._attributesByFieldName = dict((
                (attribute.fieldName, attribute)
                for attribute in cls.iterconstants()
                if hasattr(attribute, "fieldName")
            ))

        return cls._attributesByFieldName.get(fieldName, None)



class ODMatchType(Values):
    equals = ValueConstant(0x2001)
    equals.matchType = MatchType.equals

    startsWith = ValueConstant(0x2002)
    startsWith.matchType = MatchType.startsWith

    endsWith = ValueConstant(0x2003)
    endsWith.matchType = MatchType.endsWith

    contains = ValueConstant(0x2004)
    contains.matchType = MatchType.contains

    lessThan = ValueConstant(0x2005)
    lessThan.matchType = MatchType.lessThan

    greaterThan = ValueConstant(0x2006)
    greaterThan.matchType = MatchType.greaterThan

    lessThanOrEqualTo = ValueConstant(0x2007)
    lessThanOrEqualTo.matchType = MatchType.lessThanOrEqualTo

    greaterThanOrEqualTo = ValueConstant(0x2008)
    greaterThanOrEqualTo.matchType = MatchType.greaterThanOrEqualTo


    @classmethod
    def fromMatchType(cls, matchType):
        if not hasattr(cls, "_matchTypeByMatchType"):
            cls._matchTypeByMatchType = dict((
                (matchType.matchType, matchType)
                for matchType in cls.iterconstants()
            ))

        return cls._matchTypeByMatchType.get(matchType, None)



#
# Directory Service
#

class DirectoryService(BaseDirectoryService):
    """
    OpenDirectory directory service.
    """
    log = Logger()

    fieldName = ConstantsContainer(chain(
        BaseDirectoryService.fieldName.iterconstants(),
        FieldName.iterconstants()
    ))


    def __init__(self, nodeName=ODSearchPath.search.value):
        """
        @param nodeName: the OpenDirectory node to query against.
        @type nodeName: bytes
        """
        self._nodeName = nodeName


    @property
    def nodeName(self):
        return self._nodeName


    @property
    def realmName(self):
        return "OpenDirectory Node {self.nodeName!r}".format(self=self)


    @property
    def node(self):
        """
        Get the underlying (network) directory node.
        """
        if not hasattr(self, "_node"):
            try:
                self._node = odInit(self.nodeName)
            except ODError, e:
                self.log.error(
                    "OpenDirectory initialization error"
                    "(node={source.nodeName}): {error}",
                    error=e
                )
                raise OpenDirectoryError(e)

        return self._node


    @property
    def localNode(self):
        """
        Get the local node from the search path (if any), so that we can handle
        it specially.
        """
        if not hasattr(self, "_localNode"):
            if self.nodeName == ODSearchPath.search.value:
                result = getNodeAttributes(
                    self.node, ODSearchPath.search.value,
                    (ODAttribute.searchPath.value,)
                )
                if (
                    ODSearchPath.local.value in
                    result[ODAttribute.searchPath.value]
                ):
                    try:
                        self._localNode = odInit(ODSearchPath.local.value)
                    except ODError, e:
                        self.log.error(
                            "Failed to open local node: {error}}",
                            error=e,
                        )
                        raise OpenDirectoryError(e)
                else:
                    self._localNode = None

            elif self.nodeName == ODSearchPath.local.value:
                self._localNode = self.node

            else:
                self._localNode = None

        return self._localNode


    def recordsFromMatchExpression(self, expression):
        if not isinstance(expression, MatchExpression):
            raise TypeError(expression)

        matchType = ODMatchType.fromMatchType(expression.matchType)
        if matchType is None:
            raise QueryNotSupportedError(
                "Unknown match type: {0}".format(matchType)
            )

        caseInsensitive = (
            MatchFlags.caseInsensitive in iterFlags(expression.flags)
        )

        if expression.fieldName is self.fieldName.recordType:
            raise NotImplementedError()
        else:
            results = queryRecordsWithAttribute_list(
                self.node,
                ODAttribute.fromFieldName(expression.fieldName).value,
                expression.fieldValue.encode("utf-8"),
                matchType.value,
                caseInsensitive,
                [
                    recordType.value
                    for recordType in ODRecordType.iterconstants()
                ],
                [attr.value for attr in ODAttribute.iterconstants()],
            )

        # def uniqueTupleFromAttribute(self, attribute):
        #     if attribute:
        #         if isinstance(attribute, bytes):
        #             return (attribute,)
        #         else:
        #             s = set()
        #             return tuple((
        #                 (s.add(x), x)[1] for x in attribute if x not in s
        #             ))
        #     else:
        #         return ()

        for (shortName, attributes) in results:
            fields = {}

            for (key, value) in attributes.iteritems():
                if key == FieldName.metaRecordName:
                    # We get this field even though we did not ask for it...
                    continue

                try:
                    attribute = ODAttribute.lookupByValue(key)
                except ValueError:
                    self.log.debug(
                        "Got back unexpected attribute {attribute} "
                        "for record with short name {shortName}",
                        attribute=key, shortName=shortName
                    )
                    continue
                fieldName = attribute.fieldName

                try:
                    if BaseFieldName.isMultiValue(fieldName):
                        if type(value) is bytes:
                            value = (value,)
                        elif type(value) is not list:
                            raise TypeError()

                        fields[fieldName] = tuple(
                            x.decode("utf-8") for x in value
                        )

                    else:
                        if type(value) is list:
                            assert len(value) == 1
                            value = value[0]
                        elif type(value) is not bytes:
                            raise TypeError()

                        if fieldName is self.fieldName.recordType:
                            fields[fieldName] = ODRecordType.lookupByValue(
                                value
                            ).recordType
                        else:
                            fields[fieldName] = value.decode("utf-8")

                except TypeError:
                    raise AssertionError(
                        "Unexpected type {0} for attribute {1}"
                        .format(type(value), fieldName)
                    )


            yield DirectoryRecord(self, fields)


    def recordsFromExpression(self, expression):
        """
        This implementation can handle L{MatchExpression} expressions; other
        expressions are passed up to the superclass.
        """
        if isinstance(expression, CompoundExpression):
            raise NotImplementedError(Operand)

        elif isinstance(expression, MatchExpression):
            try:
                return self.recordsFromMatchExpression(expression)
            except QueryNotSupportedError:
                return BaseDirectoryService.recordsFromExpression(
                    self, expression
                )

        else:
            return BaseDirectoryService.recordsFromExpression(
                self, expression
            )




class DirectoryRecord(BaseDirectoryRecord):
    """
    OpenDirectory directory record.
    """

    def __init__(self, service, fields):
         # Make sure that uid and guid are both set and equal
        uid = fields.get(service.fieldName.uid, None)
        guid = fields.get(service.fieldName.guid, None)

        if uid is not None and guid is not None:
            if uid != guid:
                raise ValueError(
                    "uid and guid must be equal ({uid} != {guid})"
                    .format(uid=uid, guid=guid)
                )
        elif uid is None:
            fields[service.fieldName.uid] = guid
        elif guid is None:
            fields[service.fieldName.guid] = uid

        super(DirectoryRecord, self).__init__(service, fields)


    requiredFields = BaseDirectoryRecord.requiredFields + (BaseFieldName.guid,)




if __name__ == "__main__":
    service = DirectoryService()
    print(
        "Service = {service}\n"
        "Node = {service.node}\n"
        "Local node = {service.localNode}\n"
        .format(service=service)
    )

    matchMorgen = MatchExpression(
        service.fieldName.shortNames, u"sagen",
        matchType=MatchType.equals,
    )
    for record in service.recordsFromExpression(matchMorgen):
        print("*" * 80)
        print(record.description())
