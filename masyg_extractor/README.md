# masyg-extractor-server
# masyg-extractor-server

"""

users (collection)
 └── {user_id} (document)
       ├── email: string
       ├── hasUsedTrial: boolean
       ├── isSubscribed: boolean
       ├── password: string
       ├── username: string
       ├── createdAt: Timestamp
       └── updatedAt: Timestamp
       
       ├── groups (subcollection)
       │      └── {group_id} (document)
       │             ├── metadata: { ... }
       │             └── files (subcollection)
       │                      └── {file_name} (document) → { ... }
       │
       └── integrations (subcollection)
              ├── quickbooks (document)
              │      ├── config: { 
              │      │         incomeAccount: string,
              │      │         expenseAccount: string,
              │      │         paymentTerms: string,  // e.g., "Net 30" or "Net 20" if custom
              │      │         invoiceNumbering: string,
              │      │         invoiceTemplate: string,
              │      │         defaultTaxCode: string,
              │      │         discountSettings: string,
              │      │         autoInvoiceCreation: boolean,
              │      │         autoSending: boolean,
              │      │         paymentReminders: boolean,
              │      │         syncFrequency: string,  // "Real-time", "Hourly", "Daily", etc.
              │      │         errorHandling: string,
              │      │         paymentMethods: string,
              │      │         depositAccount: string,
              │      │         customerGrouping: string,
              │      │         salesReporting: string,
              │      │         // Additional QuickBooks-specific settings can be added here.
              │      │      }
              │      └── transactions (subcollection)
              │             └── {transaction_id} (document) → {
              │                     groupId: string,            // (optional) reference to a group
              │                     transactionType: string,    // e.g., "Invoice"
              │                     docNumber: string,          // e.g., "INV-12345"
              │                     customerId: string,         // e.g., "12"
              │                     date: Timestamp,            // e.g., Timestamp for the transaction date
              │                     amount: number,             // e.g., 2415.0
              │                     metadata: {                 // additional integration-specific data
              │                        syncToken: string,
              │                        otherField: string,
              │                        // any other transaction-specific details
              │                     }
              │             }
              │
              ├── xero (document)
              │      ├── config: { 
              │      │         incomeAccount: string,
              │      │         expenseAccount: string,
              │      │         paymentTerms: string,
              │      │         invoiceNumbering: string,
              │      │         invoiceTemplate: string,
              │      │         defaultTaxCode: string,
              │      │         discountSettings: string,
              │      │         autoInvoiceCreation: boolean,
              │      │         autoSending: boolean,
              │      │         paymentReminders: boolean,
              │      │         syncFrequency: string,
              │      │         errorHandling: string,
              │      │         paymentMethods: string,
              │      │         depositAccount: string,
              │      │         customerGrouping: string,
              │      │         salesReporting: string,
              │      │         // Include any additional Xero-specific credentials (e.g., clientId, clientSecret, tenantId)
              │      │      }
              │      └── transactions (subcollection)
              │             └── {transaction_id} (document) → {
              │                     groupId: string,           
              │                     transactionType: string,
              │                     docNumber: string,
              │                     customerId: string,
              │                     date: Timestamp,
              │                     amount: number,
              │                     metadata: { ... }         // any extra fields required for Xero transactions
              │             }
              │
              └── [Other integrations...]




creation of a  customer rules
here is the deal when to create a customer or not in QB: 
when the post resquest is sent 
a customer_name is sent int the request
you can assume a customer_name is required   in the request otherwise
stop the process and ask for the customer_name
but the customer_id might or might not be in the request

a) CUSTOMER ID IS IN THE REQUEST
if it in the request check  if it 
in firestore if it not save it there, then send the 
data to QB using that customer name and the customer id
if it failed send appropriate message to the user why it failed

b) CUSTOMER ID IS NOT IN THE REQUEST
if  the customer id is not in the request 
create a new customer in QB with that customer_name 
and save its customer_id return by quickbooks after 
creation its infos in firestore


creation of an  invoice rules
here is the deal when to create an invoice 
a) check if the invoice  exist in firestore by using the group_id/transaction_id 
also save the documber in QB related to as well
amd adjust acording existence that matching group_id/transaxtion_id
docnumber 
which is basically the group_id/filename assume the group_id{ filename will be in the request 
where the data where extracted from if it there 
tell the user that this invoice is already in QB

if it not create a new invoice and send it to qb


cleanedData  const input: ExtractedData = {
      group2: {
        metadata: {
          upload_time: "2024-10-01",
          file_count: 2,
          files: [
            { content: "pdf", filename: "file3.pdf" },
            { content: "pdf", filename: "file4.pdf" },
          ],
        },
        "file3.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor A",
          line_items: [
            { item_name: "A1", description: "Pipe 2 inch", quantity: 5, unit_price: "15.00" },
            { item_name: "A2", description: "Wrench", quantity: 2, unit_price: "25.50" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor B",
          line_items: [
            { item_name: "B1", description: "Hammer", quantity: 1, unit_price: "10.00" },
          ],
        },
      },
      group3: {
        metadata: {
          upload_time: "2024-10-01",
          file_count: 2,
          files: [
            { content: "pdf", filename: "file4.pdf" },
            { content: "pdf", filename: "file5.pdf" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor C",
          line_items: [
            { item_name: "C1", description: "Screwdriver Set", quantity: 3, unit_price: "8.99" },
          ],
        },
        "file5.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor D",
          line_items: [
            { item_name: "D1", description: "Pliers", quantity: 4, unit_price: "12.50" },
          ],
        },
      },
    };

    // Expected output: A flat object of uploaded files excluding metadata.
    const expectedOutput = {        
        "file3.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor A",
          line_items: [
            { item_name: "A1", description: "Pipe 2 inch", quantity: 5, unit_price: "15.00" },
            { item_name: "A2", description: "Wrench", quantity: 2, unit_price: "25.50" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor B",
          line_items: [
            { item_name: "B1", description: "Hammer", quantity: 1, unit_price: "10.00" },
          ],
        },

     
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor C",
          line_items: [
            { item_name: "C1", description: "Screwdriver Set", quantity: 3, unit_price: "8.99" },
          ],
        },
        "file5.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor D",
          line_items: [
            { item_name: "D1", description: "Pliers", quantity: 4, unit_price: "12.50" },
          ],
        },
      },
    

