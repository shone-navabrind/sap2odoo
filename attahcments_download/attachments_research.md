<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

## First: choose the right setup

There is no permanent free SharePoint Online account intended for production backups. For testing, eligible developers can obtain a renewable Microsoft 365 E5 developer sandbox with SharePoint; otherwise, SharePoint offers a limited one-month trial that becomes paid unless cancelled.[^1_1][^1_2]

Also, “all SAP attachments” can mean several different SAP repositories—such as GOS attachments, ArchiveLink documents, DMS originals, Fiori attachments, or files already stored on an external content server. The extraction method depends on which one your SAP system uses.

## Step 1: Create SharePoint test storage

1. Go to the Microsoft 365 Developer Program page and select **Join**.
2. Sign in with a Microsoft account and complete the developer-program profile.
3. If eligible, provision the Microsoft 365 E5 developer sandbox; it includes SharePoint and is for development/testing, not production backup use.[^1_3][^1_4]
4. Open the Microsoft 365 admin center, then open **SharePoint admin center**.
5. Create a site, for example `SAP-Archive`.
6. In that site, create a document library named `SAP Attachments`.
7. Enable **version history** and configure an appropriate retention/version limit. Do not treat SharePoint version history alone as a disaster-recovery backup.

For production archival, use a licensed Microsoft 365 tenant and confirm storage, retention, eDiscovery, encryption, regional/data-residency, and compliance requirements before copying business documents.

## Step 2: Identify SAP attachment source

Ask your SAP Basis or functional team to verify these four items before writing code:

- SAP product and version: ECC, S/4HANA, SAP BTP, SuccessFactors, etc.
- Attachment type: **GOS**, **ArchiveLink**, **DMS**, or a Fiori/custom attachment service.
- Physical storage today: SAP database tables, Content Repository, OpenText, filesystem, KPro, or another external server.
- Required metadata: business object type/key, document type, filename, MIME type, creator, creation time, and retention category.

This is important because SAP’s attachment relationship data and the binary file content may be in different places. ArchiveLink/CMIS connectors can make SAP save and retrieve content directly from SharePoint, but that is a product/integration decision—not merely a file-copy job.[^1_5]

## Step 3: Build a safe export pipeline

Use this architecture:

```text
SAP attachment source
  -> SAP API / approved RFC / OData / ArchiveLink interface
  -> Export worker
  -> Local staging + checksum + audit log
  -> Microsoft Graph upload
  -> SharePoint document library
  -> Metadata + verification report
```

1. **Create an Entra ID app registration** for the exporter.
2. Grant only the least permissions needed—prefer site-scoped permissions such as `Sites.Selected`, rather than broad tenant-wide write access.
3. Grant the app access only to the `SAP-Archive` SharePoint site.
4. Use Microsoft Graph to upload files into the document library:
    - Small files: single upload.
    - Larger files: upload session with chunked/resumable upload.
5. Store SAP identifiers as SharePoint columns or Graph list-item fields, such as:
    - `SAPObjectType`
    - `SAPObjectKey`
    - `SAPDocumentId`
    - `SourceRepository`
    - `OriginalFilename`
    - `SHA256`
    - `ExportedAt`
6. Make the process **idempotent**: use a stable key such as `source-system/object-type/object-key/document-id`, so reruns update/skip instead of creating duplicates.
7. Write an immutable audit record for every item: source ID, destination URL/item ID, file size, hash, result, and error message.

## Step 4: Validate before “all”

Do **not** start with every attachment. First export 20–100 representative files across different SAP business objects and sizes.

Check each item for:

- File opens correctly from SharePoint.
- File size matches SAP/source storage.
- SHA-256 hash matches before and after upload.
- SAP business-object link and metadata are preserved.
- Duplicate run does not create duplicate documents.
- Failed uploads can resume safely.
- Permissions do not expose sensitive SAP documents to unintended SharePoint users.

Once this pilot passes, run batches by date range or object type, then reconcile exported count, failed count, total bytes, and checksums against the SAP-side inventory.

What SAP system and attachment mechanism are you using—for example, S/4HANA GOS attachments, ArchiveLink, DMS, or Fiori uploads?

<span style="display:none">[^1_10][^1_6][^1_7][^1_8][^1_9]</span>

<div align="center">⁂</div>

[^1_1]: https://developer.microsoft.com/en-us/microsoft-365/dev-program

[^1_2]: https://www.microsoft.com/en-us/microsoft-365/sharepoint/collaboration

[^1_3]: https://developer.microsoft.com/en-us/microsoft-365

[^1_4]: https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program

[^1_5]: https://www.sharepoint-archivelink.com/index.html

[^1_6]: https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program-faq

[^1_7]: https://learn.microsoft.com/fil-ph/office/developer-program/microsoft-365-developer-program-faq

[^1_8]: https://learn.microsoft.com/zh-tw/office/developer-program

[^1_9]: https://www.sharepoint-archivelink.com/index_fr.html

[^1_10]: https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/tools-prerequisites


---

## Yes—if SAP exposes attachments

You can use SAP OData APIs to **list attachment metadata** and then **download each attachment as binary content**. In SAP Gateway, attachments are commonly exposed as a media entity; the actual file is retrieved from the entity’s `/$value` endpoint rather than embedded in the main list response.[^2_1][^2_2]

The first check is your service metadata:

```http
GET https://<sap-host>/sap/opu/odata/sap/<SERVICE_NAME>/$metadata
```

Look for an attachment entity set, fields such as document ID/file name/MIME type, or a media entity (`HasStream="true"`). If there is no attachment entity or media stream endpoint, OData alone cannot download all files—you will need an SAP-side custom service or the appropriate ArchiveLink/DMS API.

## Download workflow

1. **Read attachment records** in pages:
```http
GET https://<sap-host>/sap/opu/odata/sap/<SERVICE_NAME>/AttachmentSet?$top=100&$skip=0
```

2. From each response, capture the attachment key, source business object, file name, MIME type, and download/media URL.
3. **Download binary content** per attachment:
```http
GET https://<sap-host>/sap/opu/odata/sap/<SERVICE_NAME>/AttachmentSet('<ATTACHMENT_ID>')/$value
Accept: application/pdf
```

The `/$value` request should return raw bytes with a `Content-Type` and usually a `Content-Disposition` filename header; save the response as a file, not as JSON. SAP documentation describes `$value` as a way to retrieve attachment file content directly.[^2_3]

4. Verify the file size and calculate a SHA-256 checksum locally before upload.
5. Upload to SharePoint through Microsoft Graph. Use direct upload for files under 4 MB, and use `createUploadSession` for larger files or reliable resume support. Upload sessions accept sequential byte ranges and can resume after connectivity failures.[^2_4][^2_5]

## Important limitation

OData can download “all” only if the service has access to every desired repository and attachment type. A standard business OData service may show an order or invoice but not expose its GOS, ArchiveLink, or DMS files. In that case, an ABAP developer/Basis team needs to create or expose a dedicated, read-only OData service that returns:

- Attachment inventory and stable ID
- SAP object type and business key
- Filename, MIME type, size, creation timestamp
- Stream endpoint for `/$value`
- Pagination and filtering by date/object type

What is the exact SAP OData service URL or the SAP attachment type you see, so the first `AttachmentSet` request can be identified?

<span style="display:none">[^2_10][^2_6][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://community.sap.com/t5/technology-q-a/file-attachment-using-gateway/qaq-p/9144590

[^2_2]: https://help.sap.com/doc/f53c64b93e5140918d676b927a3cd65b/Cloud/en-US/docs-en/guides/features/backend-connectivity/android/using-odata-api.html

[^2_3]: https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/attachment

[^2_4]: https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0

[^2_5]: https://microsoft.github.io/entrabot/platform-docs/files-graph-api/

[^2_6]: https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-beta

[^2_7]: https://learn.microsoft.com/ko-kr/graph/api/driveitem-createuploadsession?view=graph-rest-1.0\&viewFallbackFrom=odsp-graph-online

[^2_8]: https://learn.microsoft.com/en-us/graph/whats-new-earlier

[^2_9]: https://help.sap.com/doc/f53c64b93e5140918d676b927a3cd65b/Cloud/en-US/docs-en/guides/features/backend-connectivity/mdk/odata-media.html

[^2_10]: https://api.sap.com/odata/1.0/catalog.svc/Files('f2e9cfb813a046809eb6bf66f3ec9f97')/\$value


---

## Check the standard service first

For S/4HANA systems using Harmonized Document Management, SAP provides the standard **Attachment Service API** named `API_CV_ATTACHMENT_SRV`. It can work with configured GOS, DMS, and ArchiveLink attachment frameworks, and its metadata is available at the URL below. Whether it exposes *all* existing attachments depends on your SAP release, HDM configuration, repositories, authorizations, and the attachment sources actually enabled.[^3_1]

```bash
export SAP_BASE="https://sap.example.com"
export SAP_USER="your_readonly_user"
export SAP_PASS="your_password"
export SERVICE="$SAP_BASE/sap/opu/odata/SAP/API_CV_ATTACHMENT_SRV"
```

```bash
curl --fail --silent --show-error \
  --user "$SAP_USER:$SAP_PASS" \
  "$SERVICE/\$metadata" \
  -o api_cv_attachment_metadata.xml
```

Use the metadata as the source of truth: it tells you the actual entity-set names, keys, navigation properties, and functions enabled in *your* SAP installation. OData metadata is specifically intended for clients to discover the service structure and exposed operations.[^3_2][^3_3]

## List before downloading

Do not assume an entity set or URL from an online example; extract the names from `api_cv_attachment_metadata.xml`. First inspect it:

```bash
grep -Ei "EntitySet|Attachment|Content|Stream|FunctionImport" \
  api_cv_attachment_metadata.xml
```

Then replace `AttachmentSet`, `ObjectType`, and `ObjectKey` below with the real names and values from your service metadata:

```bash
curl --fail --silent --show-error \
  --user "$SAP_USER:$SAP_PASS" \
  --header "Accept: application/json" \
  "$SERVICE/AttachmentSet?\$filter=ObjectType%20eq%20'BUS1001'%20and%20ObjectKey%20eq%20'0000001234'&\$top=100" \
  -o attachments-page-1.json
```

Start with **one known object**—for example one sales order, material, or equipment record—and confirm that returned data includes attachment IDs, filename, content type, source framework, and a media/content URL. Then paginate through the inventory rather than attempting a tenant-wide download in one call.

## Download a file

If your metadata shows a media entity (`HasStream="true"`), use the attachment key and request raw bytes through `/$value`. SAP Gateway supports media resources and raw `$value` reads.[^3_4]

```bash
export ATTACHMENT_ID="replace-with-id"
export FILE_NAME="invoice-0000001234.pdf"

curl --fail --location --silent --show-error \
  --user "$SAP_USER:$SAP_PASS" \
  --header "Accept: application/octet-stream" \
  "$SERVICE/AttachmentSet('$ATTACHMENT_ID')/\$value" \
  --output "$FILE_NAME"
```

Validate each download before sending it to SharePoint:

```bash
file "$FILE_NAME"
sha256sum "$FILE_NAME"
ls -lh "$FILE_NAME"
```

If SAP returns an attachment-specific content URL in JSON, prefer that URL rather than constructing `/$value` manually.

## Custom service decision

Use the standard service **first** if its metadata and a one-object test show every attachment source you need. SAP documents `API_CV_ATTACHMENT_SRV` as supporting configured GOS, DMS, and ArchiveLink sources, including attachment download.[^3_1]

Create a **custom read-only OData service** only when the standard API does not inventory/download your legacy repository, does not give the metadata needed for migration, cannot search globally by date/source, or cannot preserve SAP object links. The custom service should provide a paginated `AttachmentSet` plus a `HasStream` media endpoint, and should only use SAP-supported ABAP APIs—not direct database reads of attachment tables.

Because downloading is a `GET`, it normally does not require a CSRF token; CSRF protection is for state-changing calls such as POST, PUT, PATCH, and DELETE.[^3_5]

Which SAP edition and release are you using, and can you run the `$metadata` command and share only the entity-set names it returns?

<span style="display:none">[^3_10][^3_6][^3_7][^3_8][^3_9]</span>

<div align="center">⁂</div>

[^3_1]: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/9f047b05da4545ca8f9ebfc22acefd06/2e1be0a60d08400b8e4b1f6b57140a28.html

[^3_2]: https://learning.sap.com/courses/building-odata-services-with-sap-gateway/explaining-open-data-protocol-odata-

[^3_3]: https://learning.sap.com/courses/learning-the-basics-of-sap-fiori/explaining-data-services_d3477b9d-aa60-43fd-90b3-9ef71ccf75f8

[^3_4]: https://help.sap.com/docs/ABAP_PLATFORM_NEW/68bf513362174d54b58cddec28794093/97422751c639276ee10000000a445394.html

[^3_5]: https://help.sap.com/docs/SAP_NETWEAVER_750/68bf513362174d54b58cddec28794093/b35c22518bc72214e10000000a44176d.html?locale=en-US\&state=PRODUCTION\&version=7.5.29

[^3_6]: https://help.sap.com/docs/SAP_PROFITABILITY_PERFORMANCE_MANAGEMENT/2860ea0d9d464b8f91e4a9c0f652fe22/2ad7910cbd374ace9df024c3ae884c42.html

[^3_7]: https://help.sap.com/doc/cfcfced9472c462a804dee5a034a94a7/3.24/en-US/API_Guide_for_SAP_PaPM.pdf

[^3_8]: https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/36802406aebb4b96b1598246e1d316ee/bc058af0a5a64897a30dd3715b11f33d.html

[^3_9]: https://help.sap.com/saphelp_snc700_ehp04/helpdata/en/ec/aeea50ca692309e10000000a445394/content.htm

[^3_10]: https://help.sap.com/docs/SAP_IQ/a894a54d84f21015b142ffe773888f8c/3bf33be36c5f1014ab87fea4094ce065.html

